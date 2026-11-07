//! THROWAWAY persistence/FFI probe, not production financial logic or crypto protocol.
use rusqlite::{params, Connection, OptionalExtension};
use std::{
    path::Path,
    sync::{
        atomic::{AtomicBool, AtomicU8, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use zeroize::{Zeroize, Zeroizing};

uniffi::setup_scaffolding!();

#[derive(Debug, thiserror::Error, uniffi::Error)]
pub enum ProbeError {
    #[error("core is closed")]
    Closed,
    #[error("cancelled before commit")]
    Cancelled,
    #[error("probe failure: {message}")]
    Failure { message: String },
}
impl From<rusqlite::Error> for ProbeError {
    fn from(e: rusqlite::Error) -> Self {
        Self::Failure {
            message: e.to_string(),
        }
    }
}
fn failure(s: impl ToString) -> ProbeError {
    ProbeError::Failure {
        message: s.to_string(),
    }
}

#[derive(Debug, uniffi::Record)]
pub struct Snapshot {
    pub first_minor: i64,
    pub second_minor: i64,
    pub entries: i64,
    pub queued: i64,
    pub receipts: i64,
    pub schema: i64,
    pub cipher: String,
    pub provider: String,
    pub temp_store: i64,
    pub synchronous: i64,
    pub journal: String,
}

/// Real cross-thread control exposed via UniFFI. Gate names are probe-only failpoints.
#[derive(uniffi::Object)]
pub struct Control {
    cancelled: AtomicBool,
    released: AtomicBool,
    stage: AtomicU8,
    hold: u8,
}
#[uniffi::export]
impl Control {
    #[uniffi::constructor]
    pub fn new(hold: u8) -> Arc<Self> {
        Arc::new(Self {
            cancelled: AtomicBool::new(false),
            released: AtomicBool::new(false),
            stage: AtomicU8::new(0),
            hold,
        })
    }
    pub fn cancel(&self) {
        self.cancelled.store(true, Ordering::SeqCst);
    }
    pub fn release(&self) {
        self.released.store(true, Ordering::SeqCst);
    }
    pub fn stage(&self) -> u8 {
        self.stage.load(Ordering::SeqCst)
    }
}
impl Control {
    fn checkpoint(&self, stage: u8) -> Result<(), ProbeError> {
        self.stage.store(stage, Ordering::SeqCst);
        let start = Instant::now();
        while self.hold == stage && !self.released.load(Ordering::SeqCst) {
            if start.elapsed() > Duration::from_secs(8) {
                return Err(failure("gate timed out"));
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        Ok(())
    }
}

struct State {
    db: Option<Connection>,
    key: Zeroizing<Vec<u8>>,
}
#[derive(uniffi::Object)]
pub struct Engine {
    state: Mutex<State>,
    closing: AtomicBool,
}

fn open_db(path: &str, key: &[u8]) -> Result<Connection, ProbeError> {
    if key.len() != 32 {
        return Err(failure("requires a 32-byte fixture key"));
    }
    let db = Connection::open(path)?;
    // The C API avoids putting a raw key in SQL text. SQLCipher copies it internally.
    unsafe {
        let rc = rusqlite::ffi::sqlite3_key(db.handle(), key.as_ptr().cast(), key.len() as i32);
        if rc != rusqlite::ffi::SQLITE_OK {
            return Err(failure("codec rejected key"));
        }
    }
    let cipher: String = db.query_row("PRAGMA cipher_version", [], |r| r.get(0))?;
    if cipher.is_empty() {
        return Err(failure("SQLCipher missing"));
    }
    let _: i64 = db.query_row("SELECT count(*) FROM sqlite_master", [], |r| r.get(0))?;
    db.busy_timeout(Duration::from_secs(2))?;
    db.execute_batch("PRAGMA temp_store=MEMORY; PRAGMA synchronous=FULL;")?;
    Ok(db)
}
fn initialize(db: &Connection) -> Result<(), ProbeError> {
    db.execute_batch("BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, minor INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS entries(id TEXT PRIMARY KEY, kind TEXT NOT NULL, amount INTEGER NOT NULL, note TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, outcome TEXT NOT NULL);
        INSERT OR IGNORE INTO accounts VALUES(1,100000),(2,10000);
        PRAGMA user_version=1;
        COMMIT;")?;
    Ok(())
}
fn snapshot(db: &Connection) -> Result<Snapshot, ProbeError> {
    Ok(Snapshot {
        first_minor: db.query_row("SELECT minor FROM accounts WHERE id=1", [], |r| r.get(0))?,
        second_minor: db.query_row("SELECT minor FROM accounts WHERE id=2", [], |r| r.get(0))?,
        entries: db.query_row("SELECT count(*) FROM entries", [], |r| r.get(0))?,
        queued: db.query_row("SELECT count(*) FROM outbox", [], |r| r.get(0))?,
        receipts: db.query_row("SELECT count(*) FROM receipts", [], |r| r.get(0))?,
        schema: db.query_row("PRAGMA user_version", [], |r| r.get(0))?,
        cipher: db.query_row("PRAGMA cipher_version", [], |r| r.get(0))?,
        provider: db.query_row("PRAGMA cipher_provider", [], |r| r.get(0))?,
        temp_store: db.query_row("PRAGMA temp_store", [], |r| r.get(0))?,
        synchronous: db.query_row("PRAGMA synchronous", [], |r| r.get(0))?,
        journal: db.query_row("PRAGMA journal_mode", [], |r| r.get(0))?,
    })
}
#[uniffi::export]
impl Engine {
    #[uniffi::constructor]
    pub fn new(path: String, key: Vec<u8>, wal: bool) -> Result<Arc<Self>, ProbeError> {
        let key = Zeroizing::new(key);
        let db = open_db(&path, &key)?;
        let version: i64 = db.query_row("PRAGMA user_version", [], |r| r.get(0))?;
        if version == 0 {
            initialize(&db)?;
        }
        db.execute_batch(if wal {
            "PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;"
        } else {
            "PRAGMA journal_mode=DELETE;"
        })?;
        Ok(Arc::new(Self {
            state: Mutex::new(State { db: Some(db), key }),
            closing: AtomicBool::new(false),
        }))
    }
    pub fn inspect(&self) -> Result<Snapshot, ProbeError> {
        let state = self.state.lock().map_err(failure)?;
        snapshot(state.db.as_ref().ok_or(ProbeError::Closed)?)
    }
    pub fn outcome(&self, id: String) -> Result<String, ProbeError> {
        let state = self.state.lock().map_err(failure)?;
        Ok(state
            .db
            .as_ref()
            .ok_or(ProbeError::Closed)?
            .query_row("SELECT outcome FROM receipts WHERE id=?", [id], |r| {
                r.get(0)
            })
            .optional()?
            .unwrap_or("not_committed".into()))
    }
    /// Async FFI entry; cancellation uses Control, never assumed from Swift Task.cancel().
    pub async fn apply(
        &self,
        id: String,
        amount: i64,
        transfer: bool,
        control: Arc<Control>,
        crash: u8,
    ) -> Result<String, ProbeError> {
        if self.closing.load(Ordering::SeqCst) {
            return Err(ProbeError::Closed);
        }
        let mut state = self.state.lock().map_err(failure)?;
        if self.closing.load(Ordering::SeqCst) {
            return Err(ProbeError::Closed);
        }
        let db = state.db.as_mut().ok_or(ProbeError::Closed)?;
        let exists: bool = db.query_row(
            "SELECT EXISTS(SELECT 1 FROM receipts WHERE id=?)",
            [&id],
            |r| r.get(0),
        )?;
        if exists {
            return Ok("committed_duplicate".into());
        }
        if amount <= 0 {
            return Err(failure("positive amount required"));
        }
        if control.cancelled.load(Ordering::SeqCst) {
            return Err(ProbeError::Cancelled);
        }
        let tx = db.transaction()?;
        tx.execute("UPDATE accounts SET minor=minor-? WHERE id=1", [amount])?;
        control.checkpoint(1)?;
        if crash == 1 {
            std::process::exit(77);
        } // No Drop/rollback: killed-process recovery probe.
        if control.cancelled.load(Ordering::SeqCst) || self.closing.load(Ordering::SeqCst) {
            return Err(ProbeError::Cancelled);
        }
        if transfer {
            tx.execute("UPDATE accounts SET minor=minor+? WHERE id=2", [amount])?;
        }
        tx.execute(
            "INSERT INTO entries VALUES(?,?,?,?)",
            params![
                id,
                if transfer { "transfer" } else { "expense" },
                amount,
                "PROBE_SECRET_MARKER_91af"
            ],
        )?;
        tx.execute(
            "INSERT INTO outbox VALUES(?,?)",
            params![id, "PROBE_SECRET_MARKER_91af"],
        )?;
        tx.execute("INSERT INTO receipts VALUES(?,'committed')", [&id])?;
        control.checkpoint(2)?;
        if control.cancelled.load(Ordering::SeqCst) || self.closing.load(Ordering::SeqCst) {
            return Err(ProbeError::Cancelled);
        }
        tx.commit()?;
        if crash == 2 {
            std::process::exit(78);
        }
        // Outcome is already durable. Late cancellation must NOT report not saved.
        // The post-commit gate cannot turn a durable write into a failure.
        let _ = control.checkpoint(3);
        Ok(if control.cancelled.load(Ordering::SeqCst) {
            "committed_after_cancel"
        } else {
            "committed"
        }
        .into())
    }
    pub fn close(&self) -> Result<bool, ProbeError> {
        self.closing.store(true, Ordering::SeqCst);
        let mut state = self.state.lock().map_err(failure)?;
        if let Some(db) = state.db.take() {
            if let Err((db, err)) = db.close() {
                state.db = Some(db);
                return Err(err.into());
            }
        }
        state.key.zeroize();
        Ok(state.key.is_empty())
    }
    pub fn is_closing(&self) -> bool {
        self.closing.load(Ordering::SeqCst)
    }
    pub fn stress_files(&self, rollback: bool) -> Result<Vec<String>, ProbeError> {
        let state = self.state.lock().map_err(failure)?;
        let db = state.db.as_ref().ok_or(ProbeError::Closed)?;
        if rollback {
            db.execute_batch("PRAGMA journal_mode=DELETE;")?;
        }
        db.execute_batch("PRAGMA cache_size=8;")?;
        db.execute_batch(
            "CREATE TABLE IF NOT EXISTS file_probe(note TEXT);
            BEGIN IMMEDIATE;
            WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<1500)
            INSERT INTO file_probe SELECT 'PROBE_SECRET_MARKER_91af'||printf('%0200d',x) FROM n;
            COMMIT;
            BEGIN IMMEDIATE;
            UPDATE file_probe SET note=note||'modified';",
        )?;
        let path: String = db.query_row(
            "SELECT file FROM pragma_database_list WHERE name='main'",
            [],
            |r| r.get(0),
        )?;
        let mut observed = vec![];
        for suffix in ["", "-wal", "-journal", "-shm"] {
            let p = format!("{path}{suffix}");
            if Path::new(&p).exists() {
                let bytes = std::fs::read(&p).map_err(failure)?;
                if bytes
                    .windows(b"PROBE_SECRET_MARKER_91af".len())
                    .any(|w| w == b"PROBE_SECRET_MARKER_91af")
                {
                    return Err(failure(format!("plaintext marker in suffix {suffix}")));
                }
                observed.push(format!("{suffix}:{} bytes, marker absent", bytes.len()));
            }
        }
        let count: i64 = db.query_row(
            "SELECT count(*) FROM (SELECT note FROM file_probe ORDER BY note DESC)",
            [],
            |r| r.get(0),
        )?;
        if count < 1500 {
            return Err(failure("sort did not execute"));
        }
        db.execute_batch("ROLLBACK;")?;
        Ok(observed)
    }
}

/// Requires the source to be quiescent/closed and in DELETE mode, unlike a live WAL backup.
#[uniffi::export]
pub fn migrate_copy(
    source: String,
    candidate: String,
    key: Vec<u8>,
    stop: u8,
) -> Result<String, ProbeError> {
    let key = Zeroizing::new(key);
    if Path::new(&candidate).exists() {
        return Err(failure("candidate must be a new scratch path"));
    }
    if Path::new(&format!("{source}-wal")).exists() {
        return Err(failure("source must be closed, without WAL"));
    }
    std::fs::copy(&source, &candidate).map_err(failure)?;
    let mut db = open_db(&candidate, &key)?;
    let tx = db.transaction()?;
    tx.execute_batch("ALTER TABLE entries ADD COLUMN migrated_tag TEXT NOT NULL DEFAULT 'preserved'; PRAGMA user_version=2;")?;
    if stop == 1 {
        std::process::exit(81);
    }
    tx.commit()?;
    if stop == 2 {
        std::process::exit(82);
    }
    let state = snapshot(&db)?;
    if state.schema != 2 || state.entries != state.queued || state.entries != state.receipts {
        return Err(failure("candidate validation failed"));
    }
    Ok("candidate_validated_source_untouched_activation_not_performed".into())
}

/// One fixture only: does NOT select the application's wire protocol.
#[uniffi::export]
pub fn unknown_field_roundtrip(payload: String) -> Result<String, ProbeError> {
    let mut value: serde_json::Value = serde_json::from_str(&payload).map_err(failure)?;
    let object = value
        .as_object_mut()
        .ok_or_else(|| failure("object required"))?;
    object.insert("note".into(), serde_json::json!("edited by old reader"));
    serde_json::to_string(&value).map_err(failure)
}

fn sync_parent(path: &Path) -> Result<(), ProbeError> {
    std::fs::File::open(path.parent().ok_or_else(|| failure("parent required"))?)
        .map_err(failure)?
        .sync_all()
        .map_err(failure)
}
fn schema_at(path: &str, key: &[u8]) -> Result<i64, ProbeError> {
    if !Path::new(path).is_file() {
        return Err(failure("database missing"));
    }
    if Path::new(&format!("{path}-wal")).exists() {
        return Err(failure("database must be quiescent without WAL"));
    }
    let db = open_db(path, key)?;
    Ok(db.query_row("PRAGMA user_version", [], |r| r.get(0))?)
}

/// Probe-only same-directory activation. Both inputs must already be closed, without WAL.
/// Retains encrypted .previous; no key material is written to the tiny state marker.
#[uniffi::export]
pub fn activate_copy(
    source: String,
    candidate: String,
    key: Vec<u8>,
    stop: u8,
) -> Result<String, ProbeError> {
    use std::io::Write;
    let key = Zeroizing::new(key);
    if Path::new(&source).parent() != Path::new(&candidate).parent() || source == candidate {
        return Err(failure("distinct same-directory paths required"));
    }
    if schema_at(&source, &key)? != 1 || schema_at(&candidate, &key)? != 2 {
        return Err(failure("expected source v1 and validated candidate v2"));
    }
    let backup = format!("{source}.previous");
    let marker = format!("{source}.swap");
    if Path::new(&marker).exists() || Path::new(&backup).exists() {
        return Err(failure(
            "prior activation state must be inspected before a new activation",
        ));
    }
    let mut previous = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&backup)
        .map_err(failure)?;
    std::io::copy(
        &mut std::fs::File::open(&source).map_err(failure)?,
        &mut previous,
    )
    .map_err(failure)?;
    previous.sync_all().map_err(failure)?;
    let mut journal = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&marker)
        .map_err(failure)?;
    journal
        .write_all(b"probe-v1-to-v2-ready\n")
        .map_err(failure)?;
    journal.sync_all().map_err(failure)?;
    sync_parent(Path::new(&source))?;
    if stop == 1 {
        std::process::exit(83);
    }
    std::fs::rename(&candidate, &source).map_err(failure)?;
    sync_parent(Path::new(&source))?;
    if stop == 2 {
        std::process::exit(84);
    }
    std::fs::remove_file(&marker).map_err(failure)?;
    sync_parent(Path::new(&source))?;
    Ok("new_source_active_previous_retained".into())
}

/// Does not guess after an unreadable source: verifies source/backup before cleanup.
/// For the exercised atomic rename, restart observes either old v1 or new v2.
#[uniffi::export]
pub fn recover_activation(source: String, key: Vec<u8>) -> Result<String, ProbeError> {
    let key = Zeroizing::new(key);
    let marker = format!("{source}.swap");
    let backup = format!("{source}.previous");
    if !Path::new(&marker).exists() {
        schema_at(&source, &key)?;
        return Ok("nothing_to_recover".into());
    }
    if std::fs::read(&marker).map_err(failure)? != b"probe-v1-to-v2-ready\n" {
        return Err(failure("unrecognized activation marker"));
    }
    if schema_at(&backup, &key)? != 1 {
        return Err(failure("previous database not valid"));
    }
    let version = schema_at(&source, &key)?;
    if version != 1 && version != 2 {
        return Err(failure("unexpected active schema"));
    }
    std::fs::remove_file(marker).map_err(failure)?;
    sync_parent(Path::new(&source))?;
    Ok(if version == 1 {
        "old_source_preserved_candidate_not_activated"
    } else {
        "new_source_active_previous_retained"
    }
    .into())
}
