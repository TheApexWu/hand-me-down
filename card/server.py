"""The visitor screen, the operator page and the layout tool, on one small HTTP server.

  /         visitor screen (screen/index.html): handwriting crops only, nothing typed
  /op       operator page: live camera, capture, confirm, fixes, the job in play
  /layout   click the card's corners and the line box, save card/layout.json

With --lan, other machines can read the screen, cards/, jobs/ and done/, and the arm
laptop can POST its reply to /done/NNN.json, but only with the shared token: every route
refuses a request from another machine that doesn't carry it. The operator page, the layout
tool and everything that changes state answer this machine only, token or not.
"""
import builtins
import functools
import hmac
import json
import os
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2

from . import jobs, read_card, store, visit
from . import layout as lay
from .quote import words as norm_words

WEB = Path(__file__).parent / "web"
SCREEN = store.ROOT / "screen" / "index.html"
# Shown, labeled SIMULATION, when the operator says the arm is offline. Not in the repo (*.mp4 is ignored).
SIM_CLIP = Path(os.environ.get("HMD_SIM_CLIP") or store.ROOT / "screen" / "sim-demo.mp4")
# Handwriting by the team, cut out as ink (python -m card handwriting ...). Not in the repo.
HANDWRITING = {"invitation": store.ROOT / "screen" / "invitation.png", "house": store.ROOT / "screen" / "house.png"}


def ink_rect(card):
    """Where the screen's picture of this card's line sits on the card: the ink cut-out if there is one."""
    return card.get("ink_rect") or card.get("crop_rect")


def rows_in(card):
    """The written rows of a card's line, as [left, top, right, bottom] fractions of its ink picture,
    top to bottom: the screen reveals one row after another while the arm writes."""
    rect, words = ink_rect(card), [w["box"] for w in card.get("words") or [] if w.get("box")]
    if not rect or not words:
        return None
    boxes = sorted((to_crop(b, rect) for b in words), key=lambda b: b[1] + b[3] / 2)
    rows = []
    for b in boxes:
        mid = b[1] + b[3] / 2
        if rows and abs(mid - rows[-1]["mid"]) < 0.5 * b[3]:
            r = rows[-1]
            r["box"] = [min(r["box"][0], b[0]), min(r["box"][1], b[1]), max(r["box"][2], b[0] + b[2]), max(r["box"][3], b[1] + b[3])]
        else:
            rows.append({"mid": mid, "box": [b[0], b[1], b[0] + b[2], b[1] + b[3]]})
    return [[round(max(0.0, v), 4) for v in r["box"]] for r in rows]


def quote_image(card, box):
    """cards/NNN_quote.png: only the words the visitor receives, cut out of the stranger's ink.
    box is the phrase's place on the card (fractions). Returns (url, [w, h]) or (None, None)."""
    import cv2
    rect = ink_rect(card)
    img = cv2.imread(str(store.BASE / card["ink"]), cv2.IMREAD_UNCHANGED) if card.get("ink") else None
    if img is None or not rect or not box:
        return None, None
    H, W = img.shape[:2]
    l, t, w, h = to_crop(box, rect)
    px, py = 0.04 * w + 0.01, 0.16 * h
    x0, x1 = max(0, int((l - px) * W)), min(W, int((l + w + px) * W))
    y0, y1 = max(0, int((t - py) * H)), min(H, int((t + h + py) * H))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None, None
    dest = store.folder("cards") / f"{store.nnn(card['card_no'])}_quote.png"
    cv2.imwrite(str(dest), img[y0:y1, x0:x1])
    return "/" + store.rel(dest), [x1 - x0, y1 - y0]


def image_size(path):
    import cv2
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    return [int(img.shape[1]), int(img.shape[0])] if img is not None else None


def to_crop(box, rect):
    """A box in card fractions -> fractions of the line crop."""
    return [round((box[0] - rect[0]) / rect[2], 4), round((box[1] - rect[1]) / rect[3], 4),
            round(box[2] / rect[2], 4), round(box[3] / rect[3], 4)]


def quote_marks(quote, stranger, visitor):
    """Where the quoted words sit in each crop: one box on the stranger's line, and a box on
    each word of the visitor's line that the quote shares."""
    marks = {"stranger": [], "visitor": []}
    q = norm_words(quote or "")
    if not q:
        return marks
    if stranger and stranger.get("words") and ink_rect(stranger):
        flat = [(tok, i) for i, w in enumerate(stranger["words"]) for tok in norm_words(w["text"])]
        toks = [t for t, _ in flat]
        for s in range(len(toks) - len(q) + 1):
            if toks[s:s + len(q)] == q:
                idx = sorted({i for _, i in flat[s:s + len(q)]})
                rows = []                      # one box per written row, not one box across them all
                for box in (stranger["words"][i]["box"] for i in idx):
                    if rows and abs((box[1] + box[3] / 2) - (rows[-1][-1][1] + rows[-1][-1][3] / 2)) < box[3] / 2:
                        rows[-1].append(box)
                    else:
                        rows.append([box])
                marks["stranger"] += [to_crop(read_card.union(row), ink_rect(stranger)) for row in rows]
                break
    if visitor and visitor.get("words") and ink_rect(visitor):
        shared = {t for t in q if len(t) >= 4}  # skip "and", "the"
        for w in visitor["words"]:
            if shared & set(norm_words(w["text"])):
                marks["visitor"].append(to_crop(w["box"], ink_rect(visitor)))
    return marks


def lan_token():
    """The shared secret other machines must send. HMD_TOKEN if set, else one made once and kept
    in cache/lan_token (git-ignored, and outside what the Hermes container can see)."""
    if os.environ.get("HMD_TOKEN"):
        return os.environ["HMD_TOKEN"]
    path = store.folder("cache") / "lan_token"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(18))
        path.chmod(0o600)
    return path.read_text().strip()


class App:
    token = None           # set by --lan: a request from another machine must carry it
    trust_loopback = True  # tests turn this off to play the part of another machine
    sim = False            # the operator's switch: the arm is offline, play the simulation clip
    pose = {}              # the arm's last pose, as the arm laptop sent it (POST /pose), for the 3D panel

    def __init__(self, camera_spec=None, use_camera=True, image=None, reducto_call=None):
        self.camera, self.camera_spec, self.use_camera = None, camera_spec, use_camera
        self.reducto_call = reducto_call  # tests pass a stand-in; None means the real Reducto
        self.camera_error = None
        self.image = image  # a still to stand in for the camera (layout tool, testing)
        self.busy = threading.Lock()
        self.error = None
        self.visitor, self.job = None, None
        self.recorder = None
        self.stop = threading.Event()

    def cam(self):
        if self.camera is None and self.use_camera and not self.image:
            from .capture import Camera
            try:
                self.camera, self.camera_error = Camera(self.camera_spec), None
            except Exception as exc:
                self.camera_error = str(exc)
        return self.camera

    def frame(self):
        if self.image:
            from .capture import read_image
            return read_image(self.image)
        cam = self.cam()
        return cam.latest() if cam else None

    def start(self, job):
        """Run one job in the background; refuse if one is already running."""
        if not self.busy.acquire(blocking=False):
            return False

        def work():
            try:
                self.error = None
                job()
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
                store.set_state(stage="idle")
            finally:
                self.busy.release()

        threading.Thread(target=work, daemon=True).start()
        return True

    # jobs ----------------------------------------------------------------

    def capture(self, seed=False, file=None):
        frame = None
        if not file and self.image:
            file = self.image
        if not file:
            cam = self.cam()
            if cam is None:
                raise RuntimeError(self.camera_error or "no camera")
            frame = cam.capture()  # the photo is taken before anything else moves
        store.set_state(stage="reading")
        card = visit.ingest(frame=frame, file=file, call=self.reducto_call)
        if seed:
            visit.shelve(card)
            store.set_state(stage="idle")
            return
        self.visitor, self.job = card["card_no"], None
        if card.get("may_show"):
            # the public screen never shows the visitor's own card: only that it was read, and how fast
            store.set_state(stage="confirm", words=len(card.get("words") or []), read_secs=card.get("read_secs"))
        else:  # not OK to show: nothing of this card goes on the screen, so there is nothing to confirm
            self.choose(card)

    def confirm(self, yes, approve=False, text=None):
        """The visitor says the cut is their line (yes), or asks for a retake. approve is the operator's
        part: they have compared the read with the handwriting, and may have typed a correction."""
        card = store.load_card(self.visitor) if self.visitor else None
        if not card or store.state().get("stage") != "confirm":
            return
        if not yes:  # retake: this photo is dropped, the operator captures again
            visit.discard(card)
            self.visitor = None
            store.set_state(stage="idle")
            return
        if approve:
            card = visit.approve(card, text)
        self.choose(card)

    def choose(self, card):
        store.set_state(stage="choosing", candidates=self.candidates(card))
        try:
            self.job = visit.give(card)
        except jobs.NoJob as none:   # nothing the arm could write: say so to the operator, show only their line
            self.job, self.error = None, str(none)
            visit.shelve(card)
            store.set_state(stage="card")
            return
        visit.shelve(card)
        self.show_job(card)

    def another(self):
        card = store.load_card(self.visitor) if self.visitor else None
        if not card or not self.job or jobs.delivered(jobs.done(self.job["card_no"])):
            return
        tried = set(card.get("got", []))
        visit.ungive(self.job)
        store.set_state(stage="choosing", candidates=self.candidates(card))
        card["status"] = "new"  # keep the visitor's own card off the rail while choosing
        try:
            self.job = visit.give(card, exclude=tried)
            if self.job.get("house") and tried:  # nothing else was waiting: give the first one back
                jobs.job_file(self.job["card_no"]).unlink(missing_ok=True)
                self.job = visit.give(card, exclude=())
        except jobs.NoJob:                       # nothing else could be written either: the first gift stands
            self.job = visit.give(card, exclude=())
        visit.shelve(card)
        self.show_job(card)

    def test_job(self, text):
        """Send the arm a line one of us typed: no card, no chooser. For trying the arm."""
        self.visitor, self.job = None, jobs.write_test(text)   # raises jobs.NoJob with the reason
        self.show_job(None)

    def withdraw(self):
        """Take back the job in play before the arm has done it (the arm side deletes its copy)."""
        if self.job and not jobs.delivered(jobs.done(self.job["card_no"])):
            visit.ungive(self.job)
            self.job = None
            self.record_stop()
            store.set_state(stage="idle")

    def img(self, card):
        """The photo cut's URL (for the operator page), or None: a card that isn't OK to show never gets one."""
        return (card and card.get("may_show") and card.get("crop") and "/" + card["crop"]) or None

    def ink(self, card):
        """What the visitor screen shows of a card: its handwriting as ink, and the picture's size.
        Falls back to the photo cut if there is no ink cut-out. Nothing for a card not OK to show."""
        if not (card and card.get("may_show")):
            return None, None
        if card.get("ink") and (store.BASE / card["ink"]).exists():
            return "/" + card["ink"], card.get("ink_size")
        return self.img(card), None

    def screen_card(self, card, who):
        url, size = self.ink(card)
        out = {who: url, f"{who}_size": size}
        if url and card:   # the workings, for the screen: the read and where each word sits on the ink
            rect = ink_rect(card)
            out[f"{who}_line"] = card.get("line") or ""
            out[f"{who}_no"] = card["card_no"]
            out[f"{who}_words"] = [to_crop(w["box"], rect) for w in card.get("words") or [] if w.get("box")] if rect else []
            out[f"{who}_read_secs"] = card.get("read_secs")
        return out

    def candidates(self, visitor, most=6):
        """The cards the chooser is comparing this visitor's line with (all OK to show), for the screen."""
        out = []
        for c in store.waiting(exclude={visitor["card_no"]})[:most]:
            url, size = self.ink(c)
            if url and size:
                out.append({"no": c["card_no"], "ink": url, "size": size})
        return out

    def show_job(self, visitor):
        job = self.job
        stranger = None if job.get("house") else store.load_card(job["card_no"])
        # Quote mode: the public screen shows only the words the visitor receives, in the stranger's
        # own hand. Never the visitor's card, never the rest of the stranger's.
        url, size = (quote_image(stranger, job.get("quote_box")) if stranger and stranger.get("may_show") else (None, None))
        if not stranger and HANDWRITING["house"].exists():   # the house line, in the team's own hand
            url, size = "/house.png", image_size(HANDWRITING["house"])
        store.set_state(stage="writing", stranger=url, stranger_size=size,
                        by=job["picked_by"], quote=job.get("quote"), mode=job.get("mode"),
                        misses=job.get("misses", 0), pick_secs=job.get("pick_secs"),
                        job=job["card_no"], house=bool(job.get("house")), started=time.time(), est_s=jobs.expected_seconds())
        self.record_start(f"job{store.nnn(job['card_no'])}")

    def watch_done(self):
        """Follow the job in play: delivered when the arm says so, held when it refuses or goes quiet."""
        while not self.stop.wait(0.5):
            st = store.state()
            if st.get("stage") not in ("writing", "held") or not self.job:
                if self.recorder and self.recorder.auto and time.time() > self.recorder.until:
                    self.record_stop()
                continue
            reply = jobs.done(self.job["card_no"])
            waited = time.time() - st.get("started", time.time())
            if reply and reply.get("refused"):
                if st.get("held") != "refused":
                    self.arm_refused(str(reply["refused"]), st)
            elif reply:
                store.set_state(**{**st, "stage": "delivered", "held": None, "postmark": {
                    "written_at": self.job["written_at"], "delivered_at": reply.get("delivered_at"),
                    "duration_s": reply.get("duration_s")}})
                self.record_stop()
            elif st["stage"] == "writing" and waited > max(180, 2.5 * st.get("est_s", 90)):
                # no reply at all: the visitor screen must not sit on "writing" for ever
                store.set_state(**{**st, "stage": "held", "held": "late"})

    def arm_refused(self, reason, st):
        """The arm would not write this job. Never shown as delivered; the card comes off the rail."""
        card = None if self.job.get("house") else store.load_card(self.job["card_no"])
        if card:
            card.update(status="unwritable", cannot_write=f"the arm refused it: {reason}")
            card.pop("given_to", None)
            store.save_card(card)
        store.set_state(**{**st, "stage": "held", "held": "refused", "reason": reason[:200]})
        self.record_stop()

    # recordings of test runs, for checking the arm against the page ----------

    def record_start(self, label="run", auto=True):
        cam = self.cam()
        if cam is None or self.recorder:
            return None
        from .capture import Recorder
        path = store.folder("runs") / f"{time.strftime('%H%M%S')}-{label}.mp4"
        self.recorder = Recorder(cam, path, auto=auto, until=time.time() + 600)
        return path

    def record_stop(self):
        if self.recorder:
            self.recorder.stop()
            self.recorder = None

    def set_sim(self, on):
        """Arm offline: the screen plays the simulation clip, labeled as one. Returns an error or None."""
        if on and not SIM_CLIP.is_file():
            return f"no simulation clip at {store.rel(SIM_CLIP)}"
        self.sim = bool(on)
        store.set_state(**store.state())  # same state, new version: the screen redraws
        return None

    def stack(self, most=6):
        """The idle screen's loose stack: recent cards that are OK to show, newest first, as ink."""
        cards = [c for c in store.all_cards() if c.get("may_show") and c.get("status") in ("waiting", "given")]
        out = []
        for c in reversed(cards):
            url, size = self.ink(c)
            if url and size:
                out.append({"no": c["card_no"], "ink": url, "size": size})
            if len(out) == most:
                break
        made = {k: (f"/{k}.png" if p.exists() else None) for k, p in HANDWRITING.items()}
        sizes = {f"{k}_size": image_size(p) if p.exists() else None for k, p in HANDWRITING.items()}
        delivered = sum(1 for p in store.folder("done").glob("[0-9]*.json") if jobs.delivered(store.read_json(p)))
        return {"cards": out, **made, **sizes, "waiting": len(store.waiting()), "delivered": delivered}

    def status(self):
        def brief(c):
            if not c.get("may_show"):  # private: not even the operator page shows its words
                return {"no": c["card_no"], "line": "", "name": None, "ok": c.get("ok_to_show"), "private": True,
                        "error": None, "corrected": False, "at": c.get("written_at")}
            return {"no": c["card_no"], "line": c.get("line", ""), "name": c.get("name"), "ok": c.get("ok_to_show"),
                    "error": c.get("read_error"), "corrected": c.get("corrected", False), "at": c.get("written_at"),
                    "checked": bool(c.get("checked"))}
        cards = store.all_cards()
        visitor = store.load_card(self.visitor) if self.visitor else None
        layout = lay.load()
        return {
            "state": store.state(), "busy": self.busy.locked(), "error": self.error,
            "camera": self.image or (self.camera.name if self.camera else None),
            "camera_error": self.camera_error or (self.camera.error if self.camera else None),
            "visitor": visitor and {**brief(visitor), "img": self.img(visitor), "source": visitor.get("read_source")},
            "job": self.job, "done": self.job and jobs.done(self.job["card_no"]),
            "rail": [brief(c) for c in cards if c.get("status") == "waiting"],
            "unread": [{**brief(c), "error": c.get("cannot_write") or c.get("read_error")}
                       for c in cards if c.get("status") in ("unread", "unwritable")],
            "unchecked": [{**brief(c), "img": self.img(c)} for c in cards if c.get("status") == "unchecked"],
            "given": sum(c.get("status") == "given" for c in cards),
            "private": sum(c.get("status") == "private" for c in cards),
            "layout": layout, "layout_measured": lay.measured(layout),
            "recording": str(self.recorder.path.name) if self.recorder else None,
            "sim": self.sim, "sim_clip": store.rel(SIM_CLIP) if SIM_CLIP.is_file() else None,
            "sim_clip_wanted": store.rel(SIM_CLIP),
        }


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def local(self):
            return app.trust_loopback and self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

        def allowed(self):
            """This machine, or another machine carrying the shared token (header, cookie or ?token=)."""
            self.fresh_cookie = False
            if self.local():
                return True
            if not app.token:
                return False
            query = parse_qs(urlparse(self.path).query)
            cookies = dict(c.strip().split("=", 1) for c in (self.headers.get("Cookie") or "").split(";") if "=" in c)
            auth = self.headers.get("Authorization") or ""
            sent = [auth[7:] if auth.startswith("Bearer ") else "", self.headers.get("X-HMD-Token") or "",
                    cookies.get("hmd_token", ""), (query.get("token") or [""])[0]]
            ok = any(x and hmac.compare_digest(x.encode(), app.token.encode()) for x in sent)
            # a browser on a second display opens /?token=... once; the cookie carries it from then on
            self.fresh_cookie = ok and bool((query.get("token") or [""])[0])
            return ok

        def send(self, body, ctype="application/json", code=200):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if getattr(self, "fresh_cookie", False):
                self.send_header("Set-Cookie", f"hmd_token={app.token}; Path=/; HttpOnly; SameSite=Strict")
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, path, ctype=None):
            path = Path(path)
            if not path.is_file():
                return self.send({"error": "not found"}, code=404)
            types = {".jpg": "image/jpeg", ".png": "image/png", ".json": "application/json",
                     ".html": "text/html; charset=utf-8"}
            self.send(path.read_bytes(), ctype or types.get(path.suffix, "application/octet-stream"))

        def send_video(self, path):
            """An .mp4 with byte ranges, which browsers ask for before they will play or seek."""
            if not Path(path).is_file():
                return self.send({"error": "not found"}, code=404)
            data = Path(path).read_bytes()
            start, end, code = 0, len(data) - 1, 200
            rng = self.headers.get("Range") or ""
            if rng.startswith("bytes="):
                first, _, last = rng[6:].split(",")[0].partition("-")
                try:
                    start = int(first) if first else max(0, len(data) - int(last))
                    end = min(int(last), len(data) - 1) if first and last else len(data) - 1
                except ValueError:
                    start, end = 0, len(data) - 1
                if start > end or start >= len(data):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(data)}")
                    self.end_headers()
                    return
                code = 206
            body = data[start:end + 1]
            self.send_response(code)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(body)))
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.end_headers()
            self.wfile.write(body)

        def send_jpeg(self, img, width=None, quality=80):
            if img is None:
                return self.send({"error": "no frame"}, code=503)
            if width and img.shape[1] > width:
                img = cv2.resize(img, (width, round(img.shape[0] * width / img.shape[1])))
            self.send(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes(), "image/jpeg")

        def do_GET(self):
            if not self.allowed():
                return self.send({"error": "token required"}, code=401)
            url = urlparse(self.path)
            path, name, query = url.path, Path(url.path).name, parse_qs(url.query)
            if path == "/":
                return self.send_file(SCREEN)
            if path == "/state.json":
                return self.send({**store.state(), "sim": app.sim, "now": time.time()})
            if path == "/stack.json":
                return self.send(app.stack())
            if path == "/pose.json":
                return self.send(app.pose)
            if path.startswith("/sim/so101/assets/") and name.lower().endswith(".stl"):
                target = (store.ROOT / "sim" / "so101" / "assets" / name).resolve()
                if target.parent == (store.ROOT / "sim" / "so101" / "assets").resolve() and target.is_file():
                    return self.send_file(target, "model/stl")
                return self.send({"error": "not found"}, code=404)
            if path in ("/invitation.png", "/house.png"):
                return self.send_file(HANDWRITING[path[1:-4]])
            if path.startswith("/screen/") and name.endswith((".js", ".css", ".svg", ".png", ".json", ".stl", ".STL")):
                target = (store.ROOT / "screen" / path[len("/screen/"):]).resolve()
                if store.ROOT / "screen" in target.parents and target.is_file():
                    return self.send_file(target, {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
                                                   ".json": "application/json"}.get(target.suffix, "application/octet-stream"))
                return self.send({"error": "not found"}, code=404)
            if path == "/sim.mp4":
                return self.send_video(SIM_CLIP)
            for shared, ok in (("cards", (".jpg", ".png")), ("jobs", (".json",)), ("done", (".json",))):
                if path == f"/{shared}/index.json" and shared != "cards":
                    return self.send(sorted(p.stem for p in store.folder(shared).glob("[0-9]*.json")))
                if path.startswith(f"/{shared}/") and name.endswith(ok):
                    if shared == "cards":  # a card's pictures are served only if it is OK to show
                        card = store.load_card(name[:3]) if name[:3].isdigit() else None
                        if not (card and card.get("may_show")):
                            return self.send({"error": "not found"}, code=404)
                    return self.send_file(store.folder(shared) / name)
            if not self.local():
                return self.send({"error": "operator pages are local only"}, code=403)
            if path in ("/op", "/layout"):
                return self.send_file(WEB / f"{name}.html")
            if path == "/api/status":
                return self.send(app.status())
            if path == "/preview.jpg":
                return self.send_jpeg(app.frame(), width=960, quality=70)
            if path == "/frame.jpg":
                return self.send_jpeg(app.frame(), width=1920, quality=88)
            if path == "/api/layout/card.jpg":  # the card squared up with corners being tried
                frame = app.frame()
                trial = lay.load()
                try:
                    c = [float(v) for v in query["corners"][0].split(",")]
                    trial["corners"] = [[c[i], c[i + 1]] for i in range(0, 8, 2)]
                    trial["card_mm"] = [float(v) for v in query["mm"][0].split(",")]
                except (KeyError, ValueError, IndexError):
                    pass
                return self.send_jpeg(None if frame is None else lay.rectify(frame, trial), quality=88)
            self.send({"error": "not found"}, code=404)

        def do_POST(self):
            if not self.allowed():
                return self.send({"error": "token required"}, code=401)
            url = urlparse(self.path)
            path, query = url.path, parse_qs(url.query)
            length = int(self.headers.get("Content-Length") or 0)
            if length > 65536:
                return self.send({"error": "too large"}, code=413)
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except json.JSONDecodeError:
                return self.send({"error": "not JSON"}, code=400)
            arg = lambda k: body.get(k) if k in body else (query.get(k) or [None])[0]

            # the arm's pose for the 3D panel (ARM_STATUS.md): another machine may send it, with the token
            if path == "/pose":
                pos = body.get("pos") if isinstance(body, dict) else None
                joints = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")
                if not isinstance(pos, dict) or not all(isinstance(pos.get(j), (int, float)) and abs(pos[j]) <= 360 for j in joints):
                    return self.send({"error": f"pos must give degrees for {', '.join(joints)}"}, code=400)
                t = body.get("t")
                app.pose = {"pos": {j: float(pos[j]) for j in joints}, "phase": str(body.get("phase") or "")[:40],
                            "t": float(t) if isinstance(t, (int, float)) else time.time()}
                return self.send({"ok": True})
            # the arm laptop's reply: the one thing another machine may write
            if path.startswith("/done/") and path.endswith(".json"):
                stem = Path(path).stem
                if not (stem.isdigit() and jobs.job_file(stem).exists() and isinstance(body, dict)):
                    return self.send({"error": "no such job"}, code=404)
                reply = {"delivered_at": str(body["delivered_at"])[:40] if body.get("delivered_at") else None,
                         "duration_s": body["duration_s"] if isinstance(body.get("duration_s"), (int, float)) else None}
                if body.get("refused"):     # the arm would not write it: keep the reason, and never call it delivered
                    reply = {"refused": str(body["refused"])[:200], "delivered_at": None, "duration_s": None}
                store.write_json(jobs.done_file(stem), reply)
                return self.send({"ok": True})
            if not self.local():
                return self.send({"error": "local only"}, code=403)

            ok = True
            if path == "/api/capture":
                seed = str(arg("seed")) in ("1", "true", "True")
                file = arg("file")
                ok = app.start(lambda: app.capture(seed=seed, file=file))
            elif path == "/api/confirm":
                yes, approve = (str(arg(k)) in ("1", "true", "True") for k in ("yes", "approve"))
                text = arg("text")
                ok = app.start(lambda: app.confirm(yes, approve=approve, text=text))
            elif path == "/api/approve":
                card = store.load_card(arg("card"))
                if not card or not card.get("may_show"):
                    return self.send({"error": "no such card"}, code=404)
                visit.approve(card, arg("text"))
            elif path == "/api/another":
                ok = app.start(app.another)
            elif path == "/api/reread":
                card = store.load_card(arg("card"))
                ok = bool(card) and app.start(lambda: read_card.read(card))
            elif path == "/api/fix":
                card = store.load_card(arg("card"))
                if not card or not str(arg("text") or "").strip():
                    return self.send({"error": "no such card or empty text"}, code=400)
                visit.fix_line(card, arg("text"))
            elif path == "/api/clear":
                store.set_state(stage="idle")
            elif path == "/api/layout":
                keep = {k: body[k] for k in ("corners", "card_mm", "line", "name", "tick", "number", "page",
                                             "mode", "camera", "house_line", "deadline_s", "tick_ink_min") if k in body}
                lay.save({**lay.load(), **keep})
            elif path == "/api/testjob":
                text = " ".join(str(arg("text") or "").split())
                problem = "type the line first" if not text else jobs.cannot_write(text)
                if problem:
                    return self.send({"ok": False, "error": f"that line can't be written: {problem}"}, code=400)
                ok = app.start(lambda: app.test_job(text))
            elif path == "/api/withdraw":
                ok = app.start(app.withdraw)
            elif path == "/api/sim":
                problem = app.set_sim(str(arg("on")) in ("1", "true", "True"))
                if problem:
                    return self.send({"ok": False, "error": problem}, code=409)
            elif path == "/api/record":
                if app.recorder:
                    app.record_stop()
                else:
                    app.record_start(str(arg("label") or "run")[:24], auto=False)
            else:
                return self.send({"error": "not found"}, code=404)
            self.send({"ok": True} if ok else {"ok": False, "error": "busy"}, code=200 if ok else 409)

    return Handler


def my_ip():
    """This machine's address on the local network (no packet is sent)."""
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "THIS-LAPTOP-IP"
    finally:
        probe.close()


def serve(port=8040, lan=False, camera_spec=None, use_camera=True, image=None, open_page=None):
    app = App(camera_spec, use_camera, image)
    store.set_state(stage="idle")
    if lan:
        app.token = lan_token()
    httpd = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    print = functools.partial(builtins.print, flush=True)  # these lines carry the token: show them even when piped
    print(f"screen    http://localhost:{port}/")
    print(f"operator  http://localhost:{port}/op")
    print(f"layout    http://localhost:{port}/layout")
    if lan:
        ip = my_ip()
        print("LAN on. Other machines need the token on every request:")
        print(f"  arm laptop      python card/arm_sync.py --host {ip} --port {port} --token {app.token}")
        print(f"  second display  http://{ip}:{port}/?token={app.token}")
    threading.Thread(target=app.cam, daemon=True).start()  # warm the camera
    threading.Thread(target=app.watch_done, daemon=True).start()
    if open_page:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{port}{open_page}")).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop.set()
        app.record_stop()
        if app.camera:
            app.camera.close()
