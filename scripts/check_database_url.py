from __future__ import annotations

from quant_system.db.database import engine, get_database_url, init_sqlalchemy_tables


def main() -> None:
    database_url = get_database_url()
    print({"database_url": database_url, "dialect": engine.dialect.name})
    init_sqlalchemy_tables()
    if engine.dialect.name == "sqlite":
        print("SQLite table metadata initialized successfully.")
    else:
        print("Database connection configured. Schema is managed by Alembic; run `alembic upgrade head` for migrations.")


if __name__ == "__main__":
    main()
