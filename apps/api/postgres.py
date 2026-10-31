"""Small DB-API adapter preserving the application's parameterized SQL contract."""


class Record(dict):
    def __getitem__(self, key):
        return (
            list(self.values())[key]
            if isinstance(key, int)
            else super().__getitem__(key)
        )


def row_factory(cursor):
    names = [column.name for column in cursor.description or ()]
    return lambda values: Record(zip(names, values))


class Cursor:
    def __init__(self, cursor):
        self.raw = cursor

    def execute(self, sql: str, parameters=()):
        if sql.strip().upper() == "BEGIN IMMEDIATE":
            # Serialize admissions/claims across processes, as SQLite's write
            # transaction does. No network call is made while holding this lock.
            self.raw.execute("SELECT pg_advisory_xact_lock(726419083)")
        else:
            self.raw.execute(sql.replace("?", "%s"), parameters)
        return self

    @property
    def rowcount(self):
        return self.raw.rowcount

    def fetchone(self):
        return self.raw.fetchone()

    def fetchall(self):
        return self.raw.fetchall()

    def __iter__(self):
        return iter(self.raw)


class Connection:
    def __init__(self, url: str):
        import psycopg

        self.raw = psycopg.connect(url, row_factory=row_factory, connect_timeout=5)
        from apps.api.identity import tenant_id
        from apps.api.settings import saas_mode

        if saas_mode() and tenant_id() != "local":
            self.raw.execute("SET LOCAL ROLE envforge_request")
            self.raw.execute(
                "SELECT set_config('envforge.tenant_id', %s, true)", (tenant_id(),)
            )

    def cursor(self):
        return Cursor(self.raw.cursor())

    def execute(self, sql: str, parameters=()):
        return self.cursor().execute(sql, parameters)

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        if exception_type is None:
            self.raw.commit()
        else:
            self.raw.rollback()
