from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    if op.get_context().dialect.name != "sqlite":
        return
    op.execute("CREATE VIRTUAL TABLE chunk_vectors USING vec0(embedding float[384] distance_metric=cosine)")
    op.execute("INSERT INTO chunk_vectors(rowid, embedding) SELECT id, vector FROM chunks WHERE vector IS NOT NULL")
    op.execute("""CREATE TRIGGER chunk_vector_insert AFTER INSERT ON chunks WHEN NEW.vector IS NOT NULL
               BEGIN INSERT INTO chunk_vectors(rowid, embedding) VALUES(NEW.id, NEW.vector); END""")
    op.execute("""CREATE TRIGGER chunk_vector_delete AFTER DELETE ON chunks WHEN OLD.vector IS NOT NULL
               BEGIN DELETE FROM chunk_vectors WHERE rowid = OLD.id; END""")
    op.execute("""CREATE TRIGGER chunk_vector_update AFTER UPDATE OF vector ON chunks
               BEGIN
                 DELETE FROM chunk_vectors WHERE rowid = OLD.id AND OLD.vector IS NOT NULL;
                 INSERT INTO chunk_vectors(rowid, embedding) SELECT NEW.id, NEW.vector WHERE NEW.vector IS NOT NULL;
               END""")


def downgrade():
    if op.get_context().dialect.name != "sqlite":
        return
    for trigger in ("chunk_vector_insert", "chunk_vector_delete", "chunk_vector_update"):
        op.execute(f"DROP TRIGGER {trigger}")
    op.execute("DROP TABLE chunk_vectors")
