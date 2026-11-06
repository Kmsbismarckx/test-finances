"""Executable probe scenarios, not a production test suite. Uses scratch DBs only."""
from pathlib import Path
import hashlib
import html
import json
import platform
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
SCRATCH = Path(tempfile.mkdtemp(prefix="PROTOTYPE-finance-", dir=ROOT / "output"))
events = []
MARKER = b"PROBE_SECRET_MARKER_91af"


def run(name, filename, *args, expected_exit=0):
    result = subprocess.run([str(ROOT / "build/probe"), name, str(SCRATCH / filename), *map(str, args)], capture_output=True, text=True, timeout=20)
    if result.returncode != expected_exit:
        raise RuntimeError(f"{name} failed ({result.returncode}): {result.stderr[-2000:]}")
    output = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    event = output[-1] if output else {"scenario": name, "exit": result.returncode, "failpoint": args[-1] if args else None}
    event["file"] = filename
    if expected_exit:
        event["processExitWithoutDestructors"] = True
    events.append(event)
    print(json.dumps(event, ensure_ascii=False), flush=True)
    return event


def require(ok, detail):
    if not ok:
        raise RuntimeError(detail)


def balances(e, first, second, entries):
    require((e["firstMinor"], e["secondMinor"], e["entries"], e["queued"], e["receipts"]) == (first, second, entries, entries, entries), f"state mismatch: {e}")


seed = run("seed", "restart.db")
balances(seed, 99300, 10000, 1)
require(seed["cipher"] and seed["tempStore"] == 2 and seed["synchronous"] == 2, "codec/temp/durability not enabled")
balances(run("read", "restart.db"), 99300, 10000, 1)
balances(run("seed", "restart.db"), 99300, 10000, 1)
balances(run("transfer", "restart.db"), 90300, 19000, 2)

run("crash", "crash-before.db", 1, expected_exit=77)
balances(run("read", "crash-before.db"), 100000, 10000, 0)
run("crash", "crash-after.db", 2, expected_exit=78)
balances(run("read", "crash-after.db"), 91000, 19000, 1)

for gate in [1, 2, 3]:
    e = run("cancel", f"cancel-{gate}.db", gate)
    balances(e, 91000 if gate == 3 else 100000, 19000 if gate == 3 else 10000, 1 if gate == 3 else 0)
    if gate == 3:
        require(e["result"] == "committed_after_cancel" and e["retry"] == "committed_duplicate", "late cancellation lost outcome")
run("task-cancel", "swift-task-cancel.db", 1)
balances(run("close", "close.db"), 100000, 10000, 0)

run("legacy", "source-v1.db")
source = SCRATCH / "source-v1.db"
before = hashlib.sha256(source.read_bytes()).hexdigest()
for stop in [1, 2, 0]:
    target = f"candidate-{stop}.db"
    run("migrate", "source-v1.db", SCRATCH / target, stop, expected_exit={1: 81, 2: 82, 0: 0}[stop])
    observed = run("read", target)
    balances(observed, 99300, 10000, 1)
    require(observed["schema"] == (1 if stop == 1 else 2), "migration schema mismatch")
    require(hashlib.sha256(source.read_bytes()).hexdigest() == before, "migration changed source")
events.append({"scenario": "migration-source", "byteIdentical": True, "activation": "NOT IMPLEMENTED"})

for mode in ["wal", "journal"]:
    e = run(mode, f"files-{mode}.db")
    suffix = "-wal:" if mode == "wal" else "-journal:"
    require(any(item.startswith(suffix) for item in e["observedFiles"]), f"{suffix} not exercised")
before_wrong_key = hashlib.sha256(source.read_bytes()).hexdigest()
run("wrong-key", "source-v1.db")
require(hashlib.sha256(source.read_bytes()).hexdigest() == before_wrong_key, "wrong key changed source")
run("unknown-field", "unused.db")

scanned = []
for file in SCRATCH.iterdir():
    if file.is_file():
        require(MARKER not in file.read_bytes(), f"plaintext marker found in {file.name}")
        scanned.append(file.name)
events.append({"scenario": "file-scan", "markerAbsent": True, "files": sorted(scanned), "cryptographicProof": False})

environment = {}
for name, cmd in [("rust", ["rustc", "--version"]), ("swift", ["swift", "--version"]), ("xcode", ["xcodebuild", "-version"]), ("simulators", ["xcrun", "simctl", "list", "devices", "available"])]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    environment[name] = {"exit": p.returncode, "output": (p.stdout + p.stderr).strip()}
environment["host"] = {"system": platform.system(), "machine": platform.machine()}
report = {"scope": "macOS real Swift/UniFFI/Rust/SQLCipher probe", "events": events, "environment": environment, "remaining": ["iOS device/simulator builds and XCFramework/Swift Package", "real iPhone Keychain and lock/background events", "migration activation/recovery after file replacement", "actual filesystem full/I/O and power-loss faults", "complete wire/crypto protocol and compatibility matrix", "absolute erasure of Swift/FFI/SQLCipher copies is NOT claimed"]}
(ROOT / "results").mkdir(exist_ok=True)
(ROOT / "results/observations.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

page = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Технический прототип · результаты</title>
<style>body{font:16px system-ui;color:#213c34;background:#f1f4ef;max-width:960px;margin:40px auto;padding:20px}h1{font-size:32px}p{line-height:1.6}button{font:inherit;background:#285640;color:white;border:0;border-radius:9px;padding:12px;margin:4px;cursor:pointer}button[aria-pressed=true]{background:#92651e}pre{background:white;padding:22px;border:1px solid #d2ddd1;border-radius:14px;overflow:auto;line-height:1.6}aside{background:#fff2d7;padding:20px;border-radius:12px}.tabs{margin:24px 0}#state{white-space:pre-wrap}li{margin:10px 0}</style>
<h1>Swift → Rust → зашифрованная база</h1><p>Одноразовый технический прототип: выдерживает ли выбранная связка сохранение, перезапуск, отмену и прерывание миграции? Здесь воспроизводятся <b>результаты реальных запусков на macOS</b>, а не выполняется база в браузере. Повторить измерения: <code>bash prototype/run.sh</code>.</p>
<aside>Полного Xcode нет. iOS, XCFramework и блокировка физического iPhone пока не проверены. Ключ — публичная тестовая константа, данные вымышленные. Результат не подтверждает готовность защиты приложения.</aside>
<div class="tabs" id="tabs"></div><h2 id="title"></h2><p id="explain"></p><div id="state" style="background:white;padding:22px;border:1px solid #d2ddd1;border-radius:14px;line-height:1.7"></div><div><button id="reset">С начала сценария</button><button id="next">Следующее наблюдение →</button></div><h2>Произвольный просмотр</h2><div id="free"></div><h2>Оставшиеся проверки</h2><ul id="remaining"></ul><details><summary>Полные результаты и окружение</summary><pre id="raw"></pre></details>
<script>const report=__REPORT__;
const scenarios=[['Сохранение и перезапуск',['seed','read','transfer'],'Суммы — в копейках. Повтор той же команды не добавляет расход второй раз.'],['Прерывание процесса',['crash','read'],'Два отдельных запуска: выход до commit откатывает обе стороны; выход после commit сохраняет обе стороны и очередь.'],['Отмена и закрытие',['cancel','task-cancel','close'],'Явная отмена до commit откатывает, после commit возвращает сохранённый исход. Закрытие здесь вызвано вручную, не системным событием iOS.'],['Миграция',['legacy','migrate','migration-source'],'Миграция выполняется на копиях; источник побайтно неизменён. Переключение рабочего файла ещё не реализовано.'],['Файлы и совместимость',['wal','journal','wrong-key','unknown-field','file-scan'],'WAL и journal действительно созданы. Отсутствие маркера не доказывает криптографическую безопасность; сохранение неизвестного поля проверено на одном JSON-примере.']];
let model={scenario:0,step:0};
function reducer(s,a){if(a.type==='select')return{scenario:a.index,step:0};if(a.type==='reset')return{...s,step:0};if(a.type==='next')return{...s,step:Math.min(s.step+1,items(s).length-1)};if(a.type==='jump')return{...s,step:a.index};return s}
function items(s){return report.events.filter(e=>{const f=e.file||'';if(s.scenario===0)return f==='restart.db';if(s.scenario===1)return f.startsWith('crash-');if(s.scenario===2)return f.startsWith('cancel-')||f==='close.db'||f==='swift-task-cancel.db';if(s.scenario===3)return f==='source-v1.db'&&e.scenario!=='wrong-key'||f.startsWith('candidate-')||e.scenario==='migration-source';return scenarios[4][1].includes(e.scenario)})}
function renderState(event,index,total){const labels={firstMinor:'Остаток первого счёта, копейки',secondMinor:'Остаток второго счёта, копейки',entries:'Записей в истории',queued:'Изменений в очереди',receipts:'Подтверждений сохранения',schema:'Версия базы',cipher:'Проверенная библиотека шифрования',provider:'Криптографический механизм',tempStore:'Временные данные (2 = память)',synchronous:'Надёжность записи (2 = FULL)',journal:'Режим журнала',result:'Результат команды',receipt:'Сохранённый исход',retry:'Повтор команды',scenario:'Сценарий',file:'Тестовая база',exit:'Код выхода процесса',failpoint:'Точка прерывания',processExitWithoutDestructors:'Процесс вышел без очистки',controlledKeyBufferCleared:'Контролируемый буфер очищен',newCommandsRejected:'Новые команды отклоняются',hardwareLockEvent:'Реальное событие блокировки iPhone',observedFiles:'Проверенные файлы',byteIdentical:'Исходная база побайтно неизменна',activation:'Переключение рабочей базы',rejected:'Неправильный ключ отклонён',preserved:'Неизвестные данные сохранены',output:'Результат примера совместимости',markerAbsent:'Открытый маркер отсутствует',files:'Проверенные файлы',cryptographicProof:'Криптографическое доказательство'};const results={committed:'Сохранено',committed_duplicate:'Уже сохранено, повтор без дубликата',cancelled_before_commit:'Отменено до сохранения',not_committed:'Не сохранено',committed_after_cancel:'Сохранено до получения отмены',candidate_validated_source_untouched_activation_not_implemented:'Копия проверена, источник сохранён, активация ещё не реализована'};const panel=document.querySelector('#state');panel.replaceChildren();const heading=document.createElement('b');heading.textContent='Наблюдение '+index+' из '+total;panel.append(heading);Object.entries(event).forEach(([key,value])=>{const row=document.createElement('div');row.style.marginTop='8px';const label=document.createElement('strong');label.textContent=(labels[key]||key)+': ';const text=typeof value==='boolean'?(value?'Да':'Нет'):Array.isArray(value)?value.join('; '):(results[value]||String(value));row.append(label,document.createTextNode(text));panel.append(row)})}
function dispatch(a){model=reducer(model,a);render()}
function render(){const values=items(model);document.querySelector('#title').textContent=scenarios[model.scenario][0];document.querySelector('#explain').textContent=scenarios[model.scenario][2];renderState(values[model.step],model.step+1,values.length);document.querySelector('#tabs').innerHTML='';scenarios.forEach((s,i)=>{const b=document.createElement('button');b.textContent=s[0];b.setAttribute('aria-pressed',i===model.scenario);b.onclick=()=>dispatch({type:'select',index:i});document.querySelector('#tabs').append(b)});document.querySelector('#free').innerHTML='';values.forEach((e,i)=>{const b=document.createElement('button');b.textContent=(i+1)+'. '+e.scenario;b.onclick=()=>dispatch({type:'jump',index:i});document.querySelector('#free').append(b)})}
document.querySelector('#next').onclick=()=>dispatch({type:'next'});document.querySelector('#reset').onclick=()=>dispatch({type:'reset'});document.querySelector('#raw').textContent=JSON.stringify(report,null,2);report.remaining.forEach(x=>{const li=document.createElement('li');li.textContent=x;document.querySelector('#remaining').append(li)});render();</script></html>'''
page = page.replace("__REPORT__", json.dumps(report, ensure_ascii=False).replace("</", "<\\/"))
(ROOT / "results/report.html").write_text(page)
print(f"REPORT: {ROOT / 'results/report.html'}")
