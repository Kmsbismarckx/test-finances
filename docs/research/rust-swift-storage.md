# Rust–Swift, зашифрованная локальная база и доставка ядра

Исследовано 7 ноября 2026 года для [«Интеграция Rust–Swift, зашифрованное хранилище и сборка ядра»](https://github.com/Kmsbismarckx/test-finances/issues/13). Это материал для совместного [выбора инструментов](https://github.com/Kmsbismarckx/test-finances/issues/14), а не выбранный стек или результат прототипа. Код приложения не написан; сборки и испытания не выполнялись.

## Ограничения решения

[«Границы переносимого Rust-ядра и платформенных адаптеров»](https://github.com/Kmsbismarckx/test-finances/issues/6#issuecomment-6697306028) уже закрепляет финансовые правила, базу, миграции, шифрование и синхронизацию за Rust; SwiftUI вызывает предметные команды. Адаптеры дают сеть, защищённое хранение ключей, пути и lifecycle. Сохранение финансового изменения и несинхронизированного изменения атомарно; при блокировке открытая работа прекращается, рабочие ключи освобождаются. Все выпуски первой версии взаимодействуют без обязательного одновременного обновления.

Исследование не меняет ADR 0001/0002, прочитанные в рабочем каталоге: клиентское шифрование и отдельная серверная восстановительная копия ключа уже выбраны. SQLCipher защищает локальную базу, но сам по себе не задаёт шифрование сетевых записей, выдачу ключей или серверное восстановление. Windows означает современную настольную Windows; Windows Phone исключён.

## Короткий список FFI

| Вариант | Что подтверждено | Цена и вопросы для выбора |
| --- | --- | --- |
| UniFFI | Генерация Rust scaffolding и Swift/Kotlin интерфейсов из proc macros/UDL; подходит для общего предметного API. Сам инструмент не доставляет библиотеку на платформу. [Руководство](https://mozilla.github.io/uniffi-rs/latest/) | Меньше ручного bridge-кода, но нужно фиксировать генератор вместе с Rust runtime и генерируемыми файлами; Apple strict concurrency и callback reentrancy проверяются отдельно. |
| C ABI + cbindgen + тонкая Swift-обёртка | cbindgen генерирует C/C++ заголовки для публичного C API Rust. Это генератор заголовков, не готовая модель async, владения или ошибок. [Первичный репозиторий](https://github.com/mozilla/cbindgen) | Явные opaque handles, функции освобождения, pointer+length, status codes и callbacks; выше объём собственного glue-кода. Те же заголовки могут служить основой JNI/Windows interop, но эти обёртки ещё нужны. |

UniFFI предоставляет ошибки через типизированные Rust error enums и Swift `throws`; ошибки предметной команды следует отличать от ошибок хранилища/транспорта, отмены и программного дефекта. Технические подробности не должны становиться локализованными сообщениями Rust. [UniFFI errors](https://mozilla.github.io/uniffi-rs/latest/types/errors.html). В C ABI этот контракт задаётся вручную; Rust panic не должен становиться механизмом предметной ошибки или пересекать границу как обычное исключение. Правила ABI и unwind — [Rust Reference](https://doc.rust-lang.org/reference/items/external-blocks.html#abi).

### Async, отмена и завершение сессии

UniFFI умеет Rust `async fn` ↔ Swift `async` и Kotlin `suspend`; Rust executor не требуется для всякого future, но выбранные async-зависимости могут требовать собственный runtime. Публичное руководство прямо требует отдельный библиотечный канал отмены. [Async/Future support](https://mozilla.github.io/uniffi-rs/latest/futures.html).

Проверен также [шаблон Swift Async.swift на конкретном commit](https://github.com/mozilla/uniffi-rs/blob/2c73ed4a684d6f8f9dc9ee1530e86bc06cf7304b/uniffi_bindgen/src/bindings/swift/templates/Async.swift): Swift→Rust цикл polling освобождает future после завершения, а `Task.cancel()` относится к обратным Swift async callbacks при освобождении foreign future. Это разные направления; наличие второго не доказывает остановку Rust-команды при отмене Swift Task. Поэтому нельзя обещать автоматическую отмену по факту наличия `async`.

Для обоих вариантов нужен явный контракт сессии: закрыть вход новых команд, отменить сетевые callbacks, дождаться либо откатить текущую DB-транзакцию, закрыть statements/connections и освободить ключи. Отмена после commit не должна сообщать «расход не сохранён»: нужна возможность узнать исход по идентификатору команды. Это проектное следствие атомарного сохранения, а не гарантия FFI. На блокировке нельзя ждать бесконечный сетевой callback; дедлайны, обработка гонок и поведение при kill процесса требуют испытания. Деструктор Swift/Arc сам по себе не является lifecycle-протоколом: UniFFI использует счётчики ссылок и отдельные ссылки объектов. [Object references](https://mozilla.github.io/uniffi-rs/latest/internals/object_references.html).

### Секретные буферы

Ключ желательно держать в Rust-объекте с минимальным публичным API; адаптер передаёт материал из системного хранилища один раз за открытие сессии. Не использовать строки и SQL/debug logging для ключей. Это предлагаемая граница владения, а не обещание отсутствия копий: Swift Data/Array, UniFFI serialization и SQLCipher могут иметь отдельные буферы. C ABI позволяет более явно ограничить время заимствования, но ошибочные lifetime и освобождение опаснее.

`zeroize` предотвращает удаление очистки компилятором для управляемого буфера; документация отдельно предупреждает о копиях, reallocations и регистрах. Он не стирает все исторические копии, Keychain или память Swift. Проверенная документация zeroize 1.9.1 указывает Rust ≥1.85 и MIT/Apache-2.0. [Гарантии и ограничения zeroize](https://docs.rs/zeroize/latest/zeroize/). Критерий приёмки должен описывать реально контролируемые буферы, отсутствие логов и закрытие всех владельцев; абсолютную очистку памяти процесса обещать нельзя.

## Локальное хранение из Rust

Ограниченный список: `rusqlite` поверх SQLCipher Community либо `rusqlite` поверх подготовленной SQLCipher Commercial/Enterprise сборки. Обычный SQLite без codec не выполняет требование шифрованной базы. Шифрование отдельных JSON-полей с обычным SQLite не рассматривается как равноценная замена: индексы, очередь, метаданные и временные результаты потребовали бы отдельного анализа и собственного протокола.

| Сборка SQLCipher | Интеграция | Компромисс |
| --- | --- | --- |
| Community из исходников | rusqlite `bundled-sqlcipher`; вариант `bundled-sqlcipher-vendored-openssl` добавляет сборку OpenSSL | Контроль зависимостей и флагов, но команда сама поддерживает cross-compilation и обновления. |
| Подготовленная библиотека SQLCipher | rusqlite `sqlcipher`, пути headers/library и static linking задаются сборке | Требуется проверка ABI и конкретного пакета для каждого target; коммерческие условия/доступ к пакетам согласуются отдельно. |

Набор feature flags, различие bundled/system crypto и MIT-лицензия rusqlite/libsqlite3-sys подтверждены [README на зафиксированном commit](https://github.com/rusqlite/rusqlite/blob/2511d916092d01cd4ca753ad8e64d6b971b12a28/README.md). Нельзя переносить утверждение README про SecurityFramework при сборке «на Mac и для Mac» на iOS cross-build без проверки build script. В iOS-приложении нужно проверить отсутствие случайной линковки обычного системного SQLite и коллизии символов со вторым SQLite от других зависимостей.

SQLCipher Community имеет BSD-style условия с сохранением copyright/disclaimer; коммерческие продукты лицензируются отдельно. Это не лицензия Rust-приложения целиком: следует проверить лицензии фактически включённых crypto providers и всех transitive dependencies. [SQLCipher LICENSE](https://github.com/sqlcipher/sqlcipher/blob/355d4db92d3404e71faa8088d071a71d767807f4/LICENSE.md), [редакции SQLCipher](https://www.zetetic.net/sqlcipher/).

### Что зашифровано и что проверить

SQLCipher шифрует страницы основной базы, rollback journal и WAL; заголовки/метаданные файлов не следует считать секретными автоматически. Другие transient files не покрываются этим обещанием: Zetetic требует отключить файловое temp storage (`SQLITE_TEMP_STORE=2` и соответствующая конфигурация). Это нужно подтвердить для конкретного бинарника, а не только выставить PRAGMA в приложении. [SQLCipher design](https://www.zetetic.net/sqlcipher/design/).

SQLCipher принимает ключ до чтения базы, поддерживает raw key, rekey, crypto-format compatibility и `sqlcipher_export`; encryption format не равен версии схемы приложения. `cipher_migrate` решает переход старых форматов SQLCipher, а не финансовую миграцию. Неправильный ключ должен приводить к отказу без перезаписи базы. [SQLCipher API](https://www.zetetic.net/sqlcipher/sqlcipher-api/).

Проверять отдельно основной файл, `-wal`, `-journal`, `-shm`, temp directories, backup/export, crash reports и application logs после реалистичных запросов/сбоев. Отсутствие читаемого текста при поиске маркера — полезная проверка утечки, но не криптографическое доказательство. Системная file protection и Keychain не заменяются SQLCipher; точные accessibility и lock-события остаются платформенными решениями.

### Транзакция финансовых данных и очереди

Одна база, одна явная транзакция: проверка команды, обе стороны перевода/изменение операции, версия и запись очереди фиксируются одним `COMMIT`; успех возвращается после успешного commit. Сеть выполняется после него. SQLite поддерживает явные транзакции и одного писателя; `BUSY`, I/O и full disk требуют обработки. [SQLite transactions](https://www.sqlite.org/lang_transaction.html). Это реализуемый способ выполнить уже принятое требование, не автоматическое свойство наличия SQLite.

Атомарность не тождественна долговечности при потере питания: в WAL `synchronous=NORMAL` может терять недавно подтверждённые транзакции; `FULL` добавляет sync WAL на commit. Выбор журналирования и настроек следует включить в критерий «надёжно сохранено» и испытать на iOS. Неизвестные PRAGMA могут молча игнорироваться, поэтому проверять фактическое значение и codec. [SQLite PRAGMA](https://www.sqlite.org/pragma.html#pragma_synchronous).

## Миграции и совместная работа выпусков

Нужны три независимые версии: схема локальной базы; криптографический формат/идентификатор ключа; формат синхронизируемой записи и его семантика. Совместимость схемы SQLite не доказывает совместимость протокола. Версия UniFFI относится к библиотеке внутри одного приложения, а не к обмену устройств разных выпусков.

Предлагаемый предмет проверки: остановить записи, получить согласованную **зашифрованную** копию, сохранить источник неизменным, мигрировать кандидат, проверить историю/остатки/очередь и целостность, затем заменить рабочую базу с восстанавливаемым журналом состояния. Нельзя просто копировать основной файл активной WAL-базы. SQLite Online Backup API создаёт согласованный snapshot, но конкретную поддержку и ключи назначения SQLCipher нужно проверить; для смены codec-параметров исследовать `sqlcipher_export`. [SQLite Backup API](https://www.sqlite.org/backup.html), [SQLCipher API](https://www.zetetic.net/sqlcipher/sqlcipher-api/). Нужны место для двух копий, политика удаления старой копии и проверка восстановления при прерывании каждого этапа. Одна транзакция миграции не сохраняет старый файл после успешного commit. Здесь предлагается проверяемая схема, а не готовый рецепт atomic replacement для всех ОС.

Для wire-формата небольшой ориентир — JSON/Serde с явным envelope и сохранением исходного payload. `#[serde(flatten)]` может собирать неизвестные поля в map; это нужно явно хранить и сериализовать обратно. Простое игнорирование неизвестного поля теряет его при следующей записи. [Serde flatten](https://serde.rs/attr-flatten.html). Библиотека не гарантирует сохранение неизвестных вариантов enum, новых обязательных полей, точных чисел, порядка байтов или нового финансового смысла.

Следствие требования совместимости: старый клиент хранит непонятную запись/поля без потери, не пересчитывает опасные данные по старому смыслу и блокирует несовместимые команды с объяснением обновления. Потребуются правила capabilities/min-reader/min-writer и матрица пар выпусков. Если подписи/хэши зависят от точных байтов, повторная сериализация JSON не заменяет хранение оригинала. Это вопросы протокола, которые ни UniFFI, ни SQLCipher за пользователя не решают.

## Сборка и доставка

Rust перечисляет `aarch64-apple-ios` (device), `aarch64-apple-ios-sim` и `x86_64-apple-ios` (simulator) как tier 2 без host tools. Нужны Xcode SDK; ARM64 требует Xcode ≥12; текущий baseline Rust — iOS 10.0, deployment target можно повысить. Это нижняя граница Rust, не обещание минимальной iOS всего приложения: Swift concurrency, UI и остальные зависимости могут поднять её. [Rust iOS targets](https://doc.rust-lang.org/rustc/platform-support/apple-ios.html).

Сборка каждой платформенной разновидности даёт Rust static library плюс C headers/module map; устройство ARM64 и simulator ARM64 — разные targets. Apple предлагает `xcodebuild -create-xcframework` с отдельными libraries/headers для вариантов; объединять device и simulator через один fat binary нельзя. При наличии Swift framework wrapper нужны соответствующие настройки module distribution. [Создание XCFramework](https://developer.apple.com/documentation/xcode/creating-a-multi-platform-binary-framework-bundle).

Доставка имеет два уровня: XCFramework хранит binaries; Swift package содержит binary target и Swift wrapper/generated API. Можно включить XCFramework локально в package repository либо разместить ZIP удалённо с checksum; это не сборка Rust средствами SwiftPM. Пакетировать библиотеку, generated Swift и заголовки одной согласованной версии. [Binary frameworks as Swift packages](https://developer.apple.com/documentation/xcode/distributing-binary-frameworks-as-swift-packages). Потребуются зафиксированные Rust/Cargo.lock, генератор, Xcode, SDK, deployment target, C flags и crypto provider; публичного пакета для приложения первой версии пока не требуется.

CI: host Rust проверки предметных сценариев через публичный API, затем Xcode test target против **поставляемого** package на simulator; отдельные device tests для Keychain, блокировки и background lifecycle. `xcodebuild test`, test plans и result bundles дают автоматизацию, но simulator не доказывает аппаратные гарантии устройства. [Apple: running tests](https://developer.apple.com/documentation/xcode/running-tests-and-interpreting-results). Подпись/device provisioning и доступ к устройству — реальные условия такого pipeline.

## Android и Windows

Android: UniFFI имеет Kotlin bindings, но JNI loading, Gradle и packaging каждой ABI остаются интеграцией. Rust Android targets используют NDK и std; API floor следует актуальному поддерживаемому NDK, точную версию нужно фиксировать с релизом. [Rust Android](https://doc.rust-lang.org/rustc/platform-support/android.html). Проверить shared library с единственной копией SQLCipher, crypto provider, 16 KB page-size требования выбранного Android toolchain и key adapter; наличие SQLite Android API не повод отдавать владение базой Kotlin.

Windows: Rust поддерживает современные MSVC targets, включая x86_64 и ARM64; это поддержка компилятора, не готовый UI/secure-storage adapter. [Rust platform support](https://doc.rust-lang.org/rustc/platform-support.html). В официальном основном наборе UniFFI Swift/Kotlin/Python нет C#; для будущего C#/WinUI клиента нужен отдельный bridge или C ABI. [UniFFI supported languages](https://mozilla.github.io/uniffi-rs/latest/). Решение Windows UI отложено; не обещаем desktop UI из Swift bindings. SQLCipher/C dependencies потребуют Windows сборки, выбора CRT и crypto provider, проверки путей/locking и обновлений.

Лицензии инструментов: UniFFI и cbindgen — MPL-2.0; генерируемый код и включённый runtime проверяются отдельно по выбранному выпуску, нельзя автоматически считать весь продукт MPL. [UniFFI LICENSE](https://github.com/mozilla/uniffi-rs/blob/2c73ed4a684d6f8f9dc9ee1530e86bc06cf7304b/LICENSE), [cbindgen repository/license](https://github.com/mozilla/cbindgen). Коммерческие условия, экспортные ограничения и App Store требования фактических зависимостей не установлены этим исследованием.

## Что проверить до совместного выбора

1. **FFI и lifecycle:** маленький SwiftUI/Xcode harness для обоих кандидатов либо сначала более привлекательного кандидата: async команды и reverse callback, Swift strict concurrency, typed errors; блокировка во время callback/commit; отмена до и после commit, отсутствие deadlock и use-after-free. Измерить объём ручного glue, не только скорость вызова.
2. **Бинарники и секреты:** device + оба нужных simulator targets; проверить codec/version/build flags и отсутствие второго SQLite; raw key, закрытие всех объектов, controlled buffer cleanup, отсутствие секретов в логах. Нужен реальный iPhone для lock/background проверки.
3. **Crash consistency:** kill/full disk/I/O между записью финансовых данных, очереди и commit; после перезапуска допустимо всё или ничего, без половины перевода и потерянного изменения для отправки. Проверить idempotent retry после неопределённого ответа.
4. **Файлы:** заставить WAL, rollback/temp/sort, checkpoint, backup/export и rekey действительно выполняться; проверить все файлы/копии на открытые данные и recovery после interrupt. Проверить, что память/temp policy не вызывает неприемлемый расход RAM.
5. **Миграция:** fixtures всех поддерживаемых старых схем с историей и очередью; прервать каждый шаг, включая смену рабочего файла. Источник сохраняется, повторная попытка безопасна; неверный ключ не создаёт пустую новую базу поверх старой.
6. **Совместимость:** две версии клиента, новые optional fields/unknown enum/неподдерживаемый смысл, цепочка прочитать–изменить–синхронизировать; оригинальные неизвестные данные сохраняются. Проверить числа, версии ключей и конфликтующие обновления. Гарантия ограничивается согласованной матрицей выпусков первой версии.

Для [совместного выбора](https://github.com/Kmsbismarckx/test-finances/issues/14) остаются: сколько ручного FFI допустимо; кто поддерживает C/crypto builds; нужна ли коммерческая поддержка SQLCipher; минимальная iOS и доступная device CI; пределы времени закрытия сессии; размер/срок хранения migration backup; конкретный совместимый wire envelope. Если ответы требуют исполнения, оформить отдельный HITL-прототип с этими критериями, а не считать исследование положительным результатом испытаний.

## Состояние источников

Документация проверена 2026-11-07; live `/latest/` и Apple/Rust страницы могут меняться. Для воспроизводимого чтения исходников зафиксированы: UniFFI `2c73ed4a684d6f8f9dc9ee1530e86bc06cf7304b` (2026-11-06), rusqlite `2511d916092d01cd4ca753ad8e64d6b971b12a28` (2026-11-06), SQLCipher `355d4db92d3404e71faa8088d071a71d767807f4` (2026-10-26), cbindgen `2b757a22bab77981e5d414d554db7a5d6109e727` (2026-08-22). Это snapshots исследования, не выбранные production releases; стабильные версии и полный dependency audit ещё предстоят.
