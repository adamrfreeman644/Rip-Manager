"""Request bodies shared across the routers."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SettingsNode(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=4, max_length=300)
    enabled: bool = True


class SettingsUpdate(BaseModel):
    idle_poll_seconds: Optional[int] = Field(default=None, ge=2, le=300)
    active_poll_seconds: Optional[int] = Field(default=None, ge=1, le=60)
    theme: Optional[str] = Field(default=None, pattern="^(system|dark|light)$")
    sounds: Optional[bool] = None
    volume: Optional[int] = Field(default=None, ge=0, le=100)
    auto_eject: Optional[bool] = None
    confirm_eject: Optional[bool] = None
    upc_lookup: Optional[bool] = None
    metadata_musicbrainz: Optional[bool] = None
    metadata_google_books: Optional[bool] = None
    metadata_google_books_key: Optional[str] = Field(default=None, max_length=300)
    metadata_upcitemdb: Optional[bool] = None
    metadata_upcitemdb_mode: Optional[str] = Field(default=None, pattern="^(free|paid)$")
    metadata_upcitemdb_key: Optional[str] = Field(default=None, max_length=300)
    metadata_omdb: Optional[bool] = None
    metadata_omdb_key: Optional[str] = Field(default=None, max_length=300)
    minimum_video_minutes: Optional[int] = Field(default=None, ge=0, le=120)
    verify_before_eject: Optional[bool] = None
    prefer_english_audio: Optional[bool] = None
    prefer_english_subtitles: Optional[bool] = None
    drive_preferences: Optional[Dict[str, Dict[str, bool]]] = None
    dashboard_columns: Optional[int] = Field(default=None, ge=1, le=6)
    dashboard_rows: Optional[int] = Field(default=None, ge=1, le=8)
    dashboard_tiles: Optional[List[Optional[str]]] = Field(default=None, max_length=48)
    dashboard_spacing_percent: Optional[int] = Field(default=None, ge=50, le=300)
    simulation: Optional[bool] = None
    lock_enabled: Optional[bool] = None
    current_pin: Optional[str] = Field(default=None, pattern=r"^\d{4,8}$")
    new_pin: Optional[str] = Field(default=None, pattern=r"^\d{4,8}$")
    nodes: Optional[List[SettingsNode]] = None


class LoginRequest(BaseModel):
    pin: str = Field(pattern=r"^\d{4,8}$")


class NodeCreate(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=4, max_length=300)
    enabled: bool = True
    token: Optional[str] = None


class NodeUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    enabled: Optional[bool] = None
    token: Optional[str] = None


class RipRequest(BaseModel):
    """Disc details. Shared by Start Rip, Wait for Disc and Retry."""

    title: str = Field(min_length=1, max_length=160)
    year: Optional[int] = Field(default=None, ge=1880, le=2200)
    season: Optional[int] = Field(default=None, ge=0, le=999)
    disc: Optional[int] = Field(default=None, ge=0, le=999)
    barcode: Optional[str] = Field(default=None, max_length=64)
    media_type: Optional[str] = Field(default=None, pattern="^(movie|tv|music|audiobook)$")
    creator: Optional[str] = Field(default=None, max_length=160)
    narrator: Optional[str] = Field(default=None, max_length=160)

    def node_payload(self, auto_eject: Optional[bool] = None) -> dict:
        """The body sent to a Rip Node. Older nodes simply ignore extra keys."""
        payload = self.model_dump(exclude_none=True)
        if auto_eject is not None:
            payload["auto_eject"] = auto_eject
        return payload
