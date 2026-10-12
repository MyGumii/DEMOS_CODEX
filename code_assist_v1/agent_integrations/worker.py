"""Optional application-owned consumer of the durable integration queue."""
import atexit
import logging
import threading

from .store import now_ms

LOG=logging.getLogger(__name__)


class IntegrationWorker:
    def __init__(self,service,interval=5):
        self.service,self.interval=service,interval
        self.stop_event=threading.Event()
        self.thread=threading.Thread(target=self.run,name="agent-knowledge-loop",daemon=True)
        self.cursor=None

    def start(self):
        self.thread.start()
        atexit.register(self.stop)

    def stop(self):
        self.stop_event.set()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(2)

    def tick(self):
        service=self.service
        with service.store.connection() as c:
            # Cursor scans also advance past grants invalidated by canonical
            # scope changes, preventing one inaccessible task starving others.
            rows=c.execute("SELECT r.id,r.owner_id,r.connection_id FROM integration_run r JOIN agent_connection a ON a.id=r.connection_id WHERE a.revoked_at IS NULL AND a.expires_at>? AND (r.state IN ('queued','publishing','review') OR (r.state='processing' AND r.lease_until<=?)) AND (? IS NULL OR r.id>?) ORDER BY r.id LIMIT 50",
                           (now_ms(),now_ms(),self.cursor,self.cursor)).fetchall()
        self.cursor=rows[-1]["id"] if len(rows)==50 else None
        for row in rows:
            if self.stop_event.is_set():
                break
            owner,rid,cid=row["owner_id"],row["id"],row["connection_id"]
            try:
                _,config=service.authorize_connection(owner,cid,"results:write")
                run=service.store.run(owner,rid,cid=cid)
                if run["state"]=="publishing":
                    service.publish(owner,rid,cid=cid,automatic=run["publication"]["intent"]["automatic"])
                elif run["state"]=="review":
                    if config["auto_publish_sources"] and run["draft"]["automatic_eligible"]:
                        service.publish(owner,rid,cid=cid,automatic=True)
                else:
                    service.process(owner,rid,cid=cid)
            except Exception as exc:
                # No source text, credential or provider exception body in logs.
                LOG.debug("Integration %s deferred (%s)",rid,type(exc).__name__)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                LOG.warning("Integration queue scan failed; next scan will retry")
            self.stop_event.wait(self.interval)
