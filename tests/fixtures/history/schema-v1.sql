-- Historical production DDL, not a reconstruction.
-- Source: ef027e500b6edea904754cadbcafd061c2811223:run/history_store.py::_ensure_schema
-- Source file SHA256: e8b9975262fabb1f143a2ac1e34aec947ff81a3793f34368441447536bd31821

        CREATE TABLE IF NOT EXISTS history_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS history_windows (
            window_name TEXT NOT NULL,
            window_kind TEXT NOT NULL CHECK (window_kind IN ('archive', 'runtime')),
            source TEXT NOT NULL,
            session_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            rounds INTEGER NOT NULL DEFAULT 0,
            data_json TEXT NOT NULL,
            text_json TEXT NOT NULL,
            think_json TEXT NOT NULL,
            tool_json TEXT NOT NULL,
            items_json TEXT NOT NULL,
            PRIMARY KEY (window_kind, window_name)
        );
        CREATE INDEX IF NOT EXISTS idx_history_windows_session
            ON history_windows(source, session_id, window_kind, updated_at DESC);

        CREATE TABLE IF NOT EXISTS history_sessions (
            source TEXT NOT NULL,
            session_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            archive_window TEXT NOT NULL DEFAULT '',
            lifecycle TEXT NOT NULL DEFAULT 'open',
            run_state TEXT NOT NULL DEFAULT 'idle',
            title TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            rounds INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            memory_status TEXT NOT NULL DEFAULT 'unknown',
            summary_status TEXT NOT NULL DEFAULT 'none',
            record_json TEXT NOT NULL,
            PRIMARY KEY (source, session_id)
        );
        CREATE INDEX IF NOT EXISTS idx_history_sessions_list
            ON history_sessions(source, lifecycle, updated_at DESC, session_id DESC);
        CREATE INDEX IF NOT EXISTS idx_history_sessions_memory
            ON history_sessions(memory_status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_history_sessions_summary
            ON history_sessions(summary_status, updated_at);

        CREATE TABLE IF NOT EXISTS history_active_sessions (
            active_key TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            session_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS history_messages (
            window_name TEXT NOT NULL,
            message_index INTEGER NOT NULL,
            source TEXT NOT NULL,
            session_id TEXT NOT NULL,
            round_number INTEGER NOT NULL DEFAULT 0,
            role TEXT NOT NULL,
            content_text TEXT NOT NULL,
            message_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (window_name, message_index)
        );
        CREATE INDEX IF NOT EXISTS idx_history_messages_session
            ON history_messages(source, session_id, message_index);
        CREATE INDEX IF NOT EXISTS idx_history_messages_role
            ON history_messages(role, updated_at DESC);

        CREATE TABLE IF NOT EXISTS history_context_summaries (
            window_name TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            schema_version INTEGER NOT NULL,
            source_hash TEXT NOT NULL DEFAULT '',
            previous_source_hash TEXT,
            covered_through_round INTEGER NOT NULL DEFAULT 0,
            covered_rounds_json TEXT NOT NULL DEFAULT '[]',
            summary_json TEXT NOT NULL,
            memory_extractions_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_history_context_summary_session
            ON history_context_summaries(source, session_id);

        CREATE TABLE IF NOT EXISTS message_processed_messages (
            dedupe_key TEXT PRIMARY KEY,
            platform TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
            claimed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            error_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_message_processed_status_time
            ON message_processed_messages(status, updated_at);
        
INSERT INTO history_meta(key,value) VALUES('schema_version','1');
