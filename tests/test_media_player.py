import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import IN_SCOPE_DOMAINS, DeviceEntity, Room, Status
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation

requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")

# VOLUME_SET(4) | VOLUME_MUTE(8) | PREVIOUS(16) | NEXT(32) | STOP(4096) = 4156
FULL = 4 | 8 | 16 | 32 | 4096


def _player(state="playing", features=FULL, **attrs):
    base = {"supported_features": features, "media_title": "Song", "media_artist": "Artist",
            "volume_level": 0.4, "is_volume_muted": False}
    base.update(attrs)
    return DeviceEntity("media_player.tv", "Living Room TV", "media_player", state, attributes=base)


# -- model --------------------------------------------------------------------

def test_media_player_in_scope_and_controllable():
    assert "media_player" in IN_SCOPE_DOMAINS
    p = _player()
    assert p.is_media_player and p.is_controllable
    assert p.is_toggleable is False  # single press is play/pause, not a toggle
    assert p.has_long_press is True


@pytest.mark.parametrize("state,status", [
    ("playing", Status.ON),
    ("paused", Status.PENDING),
    ("buffering", Status.PENDING),
    ("idle", Status.OFF),
    ("off", Status.OFF),
    ("unavailable", Status.UNAVAILABLE),
])
def test_media_status(state, status):
    assert _player(state).status is status


def test_single_press_is_play_pause():
    assert _player().service_call() == ("media_player", "media_play_pause", "media_player.tv", {})


def test_media_capabilities_and_helpers():
    p = _player()
    assert p.supports_media_previous and p.supports_media_next and p.supports_media_stop
    assert p.supports_volume and p.supports_volume_mute
    assert p.media_title == "Song" and p.media_subtitle == "Artist"
    assert p.volume_pct == 40
    basic = _player(features=0)
    assert not (basic.supports_media_next or basic.supports_volume or basic.supports_volume_mute)


# -- navigation ---------------------------------------------------------------

def _nav(player):
    room = Room("living", "Living", entities=[player])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room],
                     on_service=lambda c: None, on_logbook=lambda e: [])
    return nav, room


@requires_assets
def test_short_press_play_pause_long_press_opens_controls(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(nav_mod.time, "monotonic", lambda: clock["t"])
    calls = []
    p = _player()
    nav, room = _nav(p)
    nav.on_service = calls.append
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
    nav.key_map = nav._build_key_map()
    key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.ENTITY)

    nav.handle_press(key, True)
    clock["t"] += 0.1
    nav.handle_press(key, False)                 # short -> play/pause
    assert calls == [("media_player", "media_play_pause", "media_player.tv", {})]
    assert nav.stack[-1].kind is FrameKind.ROOM

    clock["t"] += 1.0
    nav.handle_press(key, True)
    clock["t"] += 1.0
    nav.handle_press(key, False)                 # long -> controls shown directly (no menu)
    assert nav.stack[-1].kind is FrameKind.MEDIA


def test_play_pause_button_matches_state():
    for state, icon, label in [("playing", "pause", "Pause"), ("buffering", "pause", "Pause"),
                               ("paused", "play", "Play"), ("idle", "play", "Play")]:
        p = _player(state)
        nav, _ = _nav(p)
        nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.MEDIA, entity=p)]
        pp = next(a for a in nav._build_key_map().values()
                  if a.kind is ActionKind.SERVICE_BUTTON and a.data["call"][1] == "media_play_pause")
        assert (pp.data["icon"], pp.data["label"]) == (icon, label)


def test_fallback_icon_matches_home_assistant():
    # no artwork -> icon follows HA: explicit > device_class > default (never forced to cast)
    tv = DeviceEntity("media_player.tv", "TV", "media_player", "playing", attributes={"device_class": "tv"}, device_class="tv")
    assert icons.resolve_icon_name(tv.domain, tv.device_class, tv.explicit_icon, state=tv.state) == "television"
    custom = DeviceEntity("media_player.x", "X", "media_player", "playing", attributes={"icon": "mdi:spotify"})
    assert icons.resolve_icon_name(custom.domain, custom.device_class, custom.explicit_icon, state=custom.state) == "spotify"


def test_media_controls_include_history():
    p = _player()
    nav, _ = _nav(p)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.MEDIA, entity=p)]
    view = nav._build_key_map()
    assert any(a.kind is ActionKind.MENU_ITEM and a.data["target"] == "history" for a in view.values())


def _row(view, kind, call_filter=None):
    keys = [k for k, a in view.items()
            if a.kind is kind and (call_filter is None or call_filter(a))]
    return keys


def test_media_view_has_art_transport_and_volume():
    p = _player()
    nav, _ = _nav(p)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.MEDIA, entity=p)]
    view = nav._build_key_map()
    # 3x3 album-art mosaic in the top-left
    art_keys = sorted(k for k, a in view.items() if a.kind is ActionKind.MEDIA_ART)
    assert art_keys == [0, 1, 2, 8, 9, 10, 16, 17, 18]  # 8-wide export grid
    assert any(a.kind is ActionKind.BACK for a in view.values())
    services = [a.data["call"][1] for a in view.values() if a.kind is ActionKind.SERVICE_BUTTON]
    assert services == ["media_previous_track", "media_play_pause", "media_next_track", "media_stop",
                        "volume_down", "volume_mute", "volume_up"]
    assert any(a.kind is ActionKind.MEDIA_VOLUME for a in view.values())


def test_song_artist_album_below_the_mosaic():
    p = _player(media_album_name="A Night at the Opera")
    nav, _ = _nav(p)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.MEDIA, entity=p)]
    view = nav._build_key_map()
    # the three metadata tiles sit on the row directly below the 3x3 art (keys 24-26)
    fields = {k: a.data["field"] for k, a in view.items() if a.kind is ActionKind.MEDIA_TEXT}
    assert fields == {24: "title", 25: "artist", 26: "album"}
    assert p.media_title == "Song" and p.media_artist == "Artist" and p.media_album == "A Night at the Opera"


def test_transport_and_volume_each_on_one_line():
    p = _player()
    nav, _ = _nav(p)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.MEDIA, entity=p)]
    view = nav._build_key_map()
    cols = nav.display.cols
    transport_ids = {"media_previous_track", "media_play_pause", "media_next_track", "media_stop"}
    volume_kinds_keys = [k for k, a in view.items()
                         if a.kind is ActionKind.MEDIA_VOLUME
                         or (a.kind is ActionKind.SERVICE_BUTTON and a.data["call"][1] in {"volume_down", "volume_up", "volume_mute"})]
    transport_keys = [k for k, a in view.items()
                      if a.kind is ActionKind.SERVICE_BUTTON and a.data["call"][1] in transport_ids]
    assert len({k // cols for k in transport_keys}) == 1  # all transport on one row
    assert len({k // cols for k in volume_kinds_keys}) == 1  # all volume on one row
    assert (transport_keys[0] // cols) != (volume_kinds_keys[0] // cols)  # different rows


def test_media_view_trims_unsupported_controls():
    p = _player(features=0)  # a bare player: only play/pause, no next/prev/stop/volume
    nav, _ = _nav(p)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.MEDIA, entity=p)]
    view = nav._build_key_map()
    services = [a.data["call"][1] for a in view.values() if a.kind is ActionKind.SERVICE_BUTTON]
    assert services == ["media_play_pause"]
    assert not any(a.kind is ActionKind.MEDIA_VOLUME for a in view.values())


def test_pressing_volume_mute_toggles_mute():
    calls = []
    p = _player(is_volume_muted=False)
    nav, _ = _nav(p)
    nav.on_service = calls.append
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.MEDIA, entity=p)]
    nav.key_map = nav._build_key_map()
    mute = next(k for k, a in nav.key_map.items()
                if a.kind is ActionKind.SERVICE_BUTTON and a.data["call"][1] == "volume_mute")
    nav.handle_press(mute, True)
    assert calls == [("media_player", "volume_mute", "media_player.tv", {"is_volume_muted": True})]
    assert nav.stack[-1].kind is FrameKind.MEDIA  # stays in the media view


# -- album art (best-effort) --------------------------------------------------

def _pixels(color=(200, 30, 30)):
    """A tiny PNG as raw bytes, for the on_media_image callback."""
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


@requires_assets
def test_album_art_used_as_icon_when_available():
    art_bytes = _pixels()
    p = _player(entity_picture="/api/media_player_proxy/media_player.tv?token=x")
    room = Room("living", "Living", entities=[p])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room],
                     on_service=lambda c: None, on_media_image=lambda url: art_bytes)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
    nav.render()  # first render: art not cached yet -> fallback icon, fetch scheduled

    # the background fetch decodes + caches, then the tile uses the artwork
    import time as _t
    for _ in range(50):
        if nav._media_art:
            break
        _t.sleep(0.02)
    assert nav._media_art  # artwork was fetched and cached
    key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.ENTITY)
    with_art = nav.renderer.media_device(p, next(iter(nav._media_art.values()))).tobytes()
    without_art = nav.renderer.media_device(p, None).tobytes()
    assert with_art != without_art  # artwork changes the tile


def test_no_art_fetch_without_callback():
    p = _player(entity_picture="/api/x")
    nav, _ = _nav(p)  # no on_media_image
    assert nav._media_art_for(p) is None  # gracefully falls back, no crash
