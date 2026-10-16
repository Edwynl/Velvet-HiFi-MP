"""Thread-owned SQLite connections must not leak transactions across API calls."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import db_pool


class PoolLifecycleTests(unittest.TestCase):
    def test_close_implicitly_rolls_back_failed_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"data.db"
            a=sqlite3.connect(p, factory=db_pool._ManagedSQLiteConnection)
            a.execute("CREATE TABLE counters (id INTEGER)")
            a.commit()
            a.execute("INSERT INTO counters VALUES(1)")
            self.assertTrue(a.in_transaction)
            a.close()  # releases pooled connection, must clear write lock
            self.assertFalse(a.in_transaction)
            b=sqlite3.connect(p)
            self.assertEqual(b.execute("SELECT count(*) FROM counters").fetchone()[0],0)
            b.close()
            a.force_close()

    def test_recycler_marks_stale_instead_of_cross_thread_closing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"data.db"
            ready=threading.Event()
            proceed=threading.Event()
            details=[]
            def owner():
                con=sqlite3.connect(p, factory=db_pool._ManagedSQLiteConnection)
                ident=threading.get_ident()
                with db_pool._registry_lock:
                    db_pool._connection_registry[ident] = {
                        "connection":con, "last_used":0, "stale":False
                    }
                ready.set()
                proceed.wait(3)
                with db_pool._registry_lock:
                    details.append(db_pool._connection_registry[ident]["stale"])
                    db_pool._connection_registry.pop(ident,None)
                details.append(con.execute("SELECT 1").fetchone()[0])
                con.force_close()
            thread=threading.Thread(target=owner)
            thread.start()
            self.assertTrue(ready.wait(2))
            # Previous implementation attempted force_close() in this thread,
            # hit ProgrammingError, then lost the connection registry record.
            db_pool._recycle_pool()
            proceed.set()
            thread.join(3)
            self.assertEqual(details,[True,1])


if __name__=="__main__":
    unittest.main()
