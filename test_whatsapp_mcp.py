"""Offline unit tests with fake, nonsensitive SQLite messages (no WhatsApp required)."""
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("whatsapp_mcp", HERE/"whatsapp_mcp.py")
bridge=importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

SCHEMA="""
CREATE TABLE chats (jid TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT, last_message_ts INTEGER,
                    archived INTEGER DEFAULT 0,pinned INTEGER DEFAULT 0,unread INTEGER DEFAULT 0,unread_count INTEGER DEFAULT 0);
CREATE TABLE messages (rowid INTEGER PRIMARY KEY AUTOINCREMENT, chat_jid TEXT, chat_name TEXT,
 msg_id TEXT,sender_jid TEXT,sender_name TEXT,ts INTEGER,from_me INTEGER,text TEXT,display_text TEXT,
 media_caption TEXT,media_type TEXT,filename TEXT,is_forwarded INTEGER DEFAULT 0,edited INTEGER DEFAULT 0,
 edited_ts INTEGER DEFAULT 0, quoted_msg_id TEXT,deleted_at INTEGER);
 CREATE TABLE starred(chat_jid TEXT,msg_id TEXT, sender_jid TEXT,from_me INTEGER,starred_at INTEGER);
"""
class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.d=tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.dir=Path(self.d.name)
        with closing(sqlite3.connect(self.dir/"wacli.db")) as db:
            db.executescript(SCHEMA)
            db.executemany("INSERT INTO chats(jid,kind,name,last_message_ts) VALUES (?,?,?,?)",[
                ("123@s.whatsapp.net","dm","Alex",1720000002),
                ("456@g.us","group","Team",1720000010)])
            # Same-second messages deliberately inserted out of chat order.
            data=[
                ("123@s.whatsapp.net","Alex","m1","555@s.whatsapp.net","Alex",1720000000,0,"Please send it","Please send it"),
                ("123@s.whatsapp.net","Alex","m2","me","Me",1720000000,1,"I will Friday","I will Friday"),
                ("456@g.us","Team","m3","anon","Team",1720000000,0,"meeting tomorrow","meeting tomorrow"),
                ("456@g.us","Team","m4","me","Me",1720000010,1,"done","done"),
                ("123@s.whatsapp.net","Alex","m5","anon","Alex",1720000020,0,"removed","removed"),
            ]
            db.executemany("INSERT INTO messages(chat_jid,chat_name,msg_id,sender_jid,sender_name,ts,from_me,text,display_text) VALUES (?,?,?,?,?,?,?,?,?)",data)
            db.execute("UPDATE messages SET deleted_at=1720000030 WHERE msg_id='m5'")
            db.commit()
        self.original=bridge.STORE
        bridge.STORE=self.dir
        self.addCleanup(setattr,bridge,"STORE",self.original)

    def test_list_chats_and_activity(self):
        chats=bridge.list_chats(limit=1)
        self.assertEqual(len(chats["chats"]),1)
        self.assertTrue(chats["has_more"])
        second=bridge.list_chats(limit=1,offset=chats["next_offset"])
        self.assertEqual(second["chats"][0]["name"],"Alex")
        act=bridge.recent_activity(after="2024-07-01",before="2024-07-10")
        self.assertEqual(sum(x["message_count"] for x in act["chats"]),4)
        self.assertEqual(sum(x["sent_by_me"] for x in act["chats"]),2)

    def test_incremental_no_loss_and_snapshot(self):
        p=bridge.get_new_messages(after_rowid=0,limit=2)
        self.assertTrue(p["has_more"])
        snapshot=p["snapshot_rowid"]
        self.assertEqual(snapshot,5)
        with closing(sqlite3.connect(self.dir/"wacli.db")) as db:
            db.execute("INSERT INTO messages(chat_jid,msg_id,ts,from_me,text,display_text) VALUES (?,?,?,?,?,?)",("123@s.whatsapp.net","m6",1720000040,1,"later","later"))
            db.commit()
        q=bridge.get_new_messages(after_rowid=p["next_rowid"],snapshot_rowid=snapshot,limit=2)
        self.assertFalse(q["has_more"])
        self.assertEqual([m["message_id"] for m in p["messages"]+q["messages"]],["m1","m2","m3","m4"])
        self.assertEqual(q["next_rowid"],5) # include last tombstone as processed watermark
        newer=bridge.get_new_messages(after_rowid=5)
        self.assertEqual([m["message_id"] for m in newer["messages"]],["m6"])

    def test_same_second_cursor_does_not_lose_messages(self):
        cursor=""
        items=[]
        for i in range(5):
            page=bridge.get_messages(after="2024-07-01",before="2024-07-10",limit=1,cursor=cursor)
            items+= [m["message_id"] for m in page["messages"]]
            if not page["has_more"]:
                break
            cursor=page["next_cursor"]
        self.assertEqual(items,["m4","m3","m2","m1"])
        self.assertFalse(page["has_more"])

    def test_query_and_direction(self):
        rows=bridge.get_messages(direction="outgoing",after="2024-07-01",before="2024-07-10")
        self.assertEqual({r["message_id"] for r in rows["messages"]},{"m2","m4"})
        matches=bridge.search_messages_paged("meeting",after="2024-07-01",before="2024-07-10")
        self.assertEqual(matches["messages"][0]["message_id"],"m3")
        self.assertEqual(bridge.search_messages_paged("' OR 1=1 --")["count"],0)

    def test_changes_and_starred(self):
        with closing(sqlite3.connect(self.dir/"wacli.db")) as db:
            db.execute("UPDATE messages SET edited=1, edited_ts=1720000050 WHERE msg_id='m2'")
            db.execute("INSERT INTO starred(chat_jid,msg_id,starred_at) VALUES ('123@s.whatsapp.net','m1',1720000080)")
            db.commit()
        changed=bridge.get_edited_or_deleted_messages(after="2024-07-01",before="2024-07-10")
        self.assertEqual({x["message_id"] for x in changed["changes"]},{"m2","m5"})
        deleted=next(x for x in changed["changes"] if x["deleted"])
        self.assertIsNone(deleted["text"])
        starred=bridge.get_starred_messages(after="2024-07-01",before="2024-07-10")
        self.assertEqual(starred["messages"][0]["message_id"],"m1")

    def test_initial_filter_and_no_flag_injection(self):
        msgs=bridge.get_new_messages(after_rowid=0,after="2024-07-04",limit=10)
        self.assertEqual(msgs["messages"],[])
        with self.assertRaises(ValueError):
            bridge.search_messages_fast("--output=/tmp/bad")

    def test_read_only_sqlite_cannot_write(self):
        with bridge._db() as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM messages")

    def test_cli_always_readonly_no_shell(self):
        import subprocess
        ok=subprocess.CompletedProcess(args=[],returncode=0,stdout='{"messages":[]}',stderr='')
        with patch.object(bridge.subprocess,"run",return_value=ok) as runner:
            bridge.search_messages_fast("meeting",limit=3)
            cmd=runner.call_args.args[0]
            self.assertEqual(cmd[:6],[bridge.WACLI,"--store",str(self.dir),"--read-only","--json","messages"])
            self.assertEqual(runner.call_args.kwargs["env"]["WACLI_READONLY"],"1")
            self.assertFalse(runner.call_args.kwargs.get("shell",False))

    def test_bad_parameters_rejected(self):
        tests=[lambda:bridge.get_new_messages(after_rowid=-1),
               lambda:bridge.get_messages(cursor="bad"),
               lambda:bridge.get_messages(chat_jid="hi;rm -rf /"),
               lambda:bridge.get_messages(limit=1000),
               lambda:bridge.get_messages(after="2026-01-01",before="2025-01-01"),
               lambda:bridge.get_message_context("a@g.us","m1",before=41),
               lambda:bridge.search_messages_paged(""),
               lambda:bridge.list_chats(kind="malware")]
        for t in tests:
            with self.subTest(t=t),self.assertRaises(ValueError):t()

    def test_no_privileged_tools(self):
        names={f.__name__ for f in bridge.TOOLS}
        self.assertTrue({"get_new_messages","recent_activity","search_messages_fast","history_coverage"}.issubset(names))
        self.assertFalse(any(x in names for x in ("send_message","sync","delete_message","run_command")))

if __name__=="__main__": unittest.main(verbosity=2)
