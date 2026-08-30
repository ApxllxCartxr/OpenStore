from sqlmodel import SQLModel, create_engine, Session
from reference.merchant.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
)

def init_db():
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session