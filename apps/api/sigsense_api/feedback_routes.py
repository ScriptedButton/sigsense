"""HTTP API for correcting classifications (see feedback.py / learning.py)."""

from __future__ import annotations

import time
from typing import Callable

import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from sigsense_api.feedback import FeedbackError

MAX_IMPORT_BYTES = 80 * 1024 * 1024


class FeedbackRequest(BaseModel):
    # Id of a track in the tracks list the client was shown.
    track_id: str = Field(min_length=1, max_length=96)
    # Rows-path (RTL/OpenWebRX/Demo) tracks live in a per-browser session.
    session: str | None = Field(default=None, max_length=64)
    # Any class name, a custom label, or "__noise__" for "not a signal".
    label: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=200)


def build_router(
    get_engine: Callable,
    global_tracker,
    session_trackers: Callable[[str | None], list],
) -> APIRouter:
    """`session_trackers(session)` lists the per-session trackers to search."""
    router = APIRouter(prefix="/classify", tags=["classify"])

    def locate(track_id: str, session: str | None):
        for candidate in [global_tracker, *session_trackers(session)]:
            with candidate.lock:
                if track_id in candidate.tracks:
                    return candidate
        return None

    def refresh(eng, trackers) -> int:
        """Re-evaluate every captured track so labels follow the new evidence now."""
        changed = 0
        for t in trackers:
            for track_id in list(t.captures):
                cap = t.capture_for(track_id)
                if not cap:
                    continue
                decided = eng.decide(cap["cnn_probs"], cap["embedding"])
                with t.lock:
                    previous = t.tracks.get(track_id, {}).get("label")
                if t.relabel(
                    track_id,
                    decided["label"],
                    decided["confidence"],
                    learned=decided["learned"],
                    alternatives=decided["alternatives"],
                ) and previous != decided["label"]:
                    changed += 1
        return changed

    def all_trackers(session: str | None) -> list:
        return [global_tracker, *session_trackers(session)]

    @router.get("/classes")
    def classes() -> dict:
        eng = get_engine()
        store = eng.feedback
        return {
            "classes": list(eng.model.classes),
            "custom": store.custom_labels(),
            "noise_label": "Noise",
        }

    @router.post("/feedback")
    def submit(body: FeedbackRequest) -> dict:
        eng = get_engine()
        owner = locate(body.track_id, body.session)
        if owner is None:
            raise HTTPException(
                404, "That signal is no longer being tracked. Select it again."
            )
        cap = owner.capture_for(body.track_id)
        if cap is None:
            raise HTTPException(
                422,
                "This signal has nothing captured to learn from "
                "(Bluetooth devices are identified from decoded packets).",
            )
        with owner.lock:
            shown = owner.tracks[body.track_id].get("label")
        try:
            item = eng.feedback.add(
                cap["spec"].astype(np.float32),
                body.label,
                note=body.note,
                wrong_label=shown,
                track_id=body.track_id,
                source="rows" if owner is not global_tracker else "iq",
            )
        except FeedbackError as exc:
            raise HTTPException(400, str(exc)) from exc

        track = owner.pin_label(body.track_id, item["label"], now=time.time())
        # What the model alone now says about this same input (the pin is only
        # for this track; this shows that *similar* signals will follow).
        decided = eng.decide(cap["cnn_probs"], cap["embedding"])
        refreshed = refresh(eng, all_trackers(body.session))
        return {
            "item": item,
            "track": track,
            "prediction": {
                "label": decided["label"],
                "confidence": decided["confidence"],
                "alternatives": decided["alternatives"],
                "learned": decided["learned"],
                "was": shown,
            },
            "refreshed": refreshed,
            "counts": eng.feedback.list()["counts"],
        }

    @router.get("/feedback")
    def list_feedback() -> dict:
        return get_engine().feedback.list()

    @router.get("/feedback/export")
    @router.post("/feedback/export")
    def export() -> Response:
        data = get_engine().feedback.export_zip()
        return Response(
            data,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="sigsense-corrections.zip"'},
        )

    @router.post("/feedback/import")
    async def import_feedback(file: UploadFile = File(...)) -> dict:
        data = await file.read(MAX_IMPORT_BYTES + 1)
        if len(data) > MAX_IMPORT_BYTES:
            raise HTTPException(413, "Export is too large")
        eng = get_engine()
        try:
            added = eng.feedback.import_zip(data)
        except FeedbackError as exc:
            raise HTTPException(400, str(exc)) from exc
        refresh(eng, all_trackers(None))
        return {"added": added, "total": eng.feedback.list()["total"]}

    @router.get("/feedback/{example_id}/thumbnail.png")
    def thumbnail(example_id: str) -> Response:
        png = get_engine().feedback.thumbnail(example_id)
        if png is None:
            raise HTTPException(404, "No such correction")
        return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})

    def unpin(items: list[dict]) -> None:
        """Retracting a correction also releases the track it was pinned to."""
        for item in items:
            if item.get("track_id"):
                for t in all_trackers(None):
                    t.unpin(item["track_id"])

    @router.delete("/feedback/{example_id}")
    def delete_one(example_id: str) -> dict:
        eng = get_engine()
        before = [i for i in eng.feedback.list()["items"] if i["id"] == example_id]
        if not eng.feedback.remove(example_id):
            raise HTTPException(404, "No such correction")
        unpin(before)
        refresh(eng, all_trackers(None))
        return {"removed": 1, "counts": eng.feedback.list()["counts"]}

    @router.delete("/feedback")
    def delete_many(label: str | None = None, all: bool = False) -> dict:
        eng = get_engine()
        listed = eng.feedback.list()["items"]
        if all:
            before = listed
            removed = eng.feedback.clear()
        elif label:
            before = [i for i in listed if i["label"].lower() == label.lower()]
            removed = eng.feedback.remove_label(label)
        else:
            raise HTTPException(400, "Give ?label=NAME or ?all=true")
        unpin(before)
        refresh(eng, all_trackers(None))
        return {"removed": removed, "counts": eng.feedback.list()["counts"]}

    return router
