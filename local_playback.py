"""
MusicIQ Local Playback Module
────────────────────────────
Windows audio output using WASAPI/ASIO.

Requirements:
  • sounddevice: pip install sounddevice
  • For ASIO support: Install ASIO drivers for your audio interface

Supported output modes:
  - WASAPI Shared: Default Windows audio (works out of box)
  - WASAPI Exclusive: Low-latency direct access to audio device
  - ASIO: Professional audio interface drivers (requires ASIO hardware)
"""

import os
import threading
from pathlib import Path
from typing import Optional

from dsp_engine import build_dsp_filter_chain

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False
    print("WARNING: sounddevice not installed. Local playback disabled.")
    print("Install with: pip install sounddevice")

log = __import__("logging").getLogger("musiciq.playback")

# Global playback state
_playback_state = {
    "playing": False,
    "paused": False,
    "track_id": None,
    "position": 0,
    "device": None,
    "volume": 1.0,
}

_state_lock = threading.Lock()
_playback_thread: Optional[threading.Thread] = None

UPSAMPLE_MULTIPLIERS = {
    "none": 1,
    "2x": 2,
    "4x": 4,
    "8x": 8,
    "16x": 16,
}


def _get_library_db_path() -> Path:
    """Resolve the active library database path for this project."""
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        return Path(data_dir) / "library.db"

    try:
        from settings import get_settings

        return get_settings().db_path
    except Exception:
        return Path("velvet_data") / "library.db"


def get_output_devices() -> list[dict]:
    """Get list of available audio output devices."""
    if not SOUNDDEVICE_AVAILABLE:
        return []

    try:
        devices = sd.query_devices()
        if isinstance(devices, dict):
            return [devices]
        return devices
    except Exception as e:
        log.warning(f"Failed to query devices: {e}")
        return []


def get_output_device_by_name(name_contains: str) -> Optional[int]:
    """Find output device ID by name substring."""
    if not SOUNDDEVICE_AVAILABLE:
        return None

    try:
        devices = sd.query_devices()
        if isinstance(devices, list):
            for i, dev in enumerate(devices):
                if dev["max_output_channels"] > 0 and name_contains.lower() in dev["name"].lower():
                    return i
        elif isinstance(devices, dict) and devices["max_output_channels"] > 0:
            if name_contains.lower() in devices["name"].lower():
                return devices["index"]
    except Exception as e:
        log.warning(f"Failed to find device: {e}")
    return None


def _audio_stream_callback(outdata, frames, time_info, status):
    """Callback for audio streaming - called by sounddevice."""
    if status:
        log.warning(f"Audio status: {status}")
    # This is a placeholder - actual implementation would read from file/stream
    outdata.fill(0)


def _resolve_output_sample_rate(source_rate: Optional[float], upsample: str, device_rate: float) -> int:
    base_rate = int(source_rate or device_rate or 48000)
    multiplier = UPSAMPLE_MULTIPLIERS.get(upsample or "none", 1)
    return max(8000, int(base_rate * multiplier))


class LocalPlayer:
    """Local audio player using sounddevice."""

    def __init__(self, device_id: Optional[int] = None, latency: str = "low"):
        """
        Initialize local player.

        Args:
            device_id: Audio device index (None for default)
            latency: "low", "medium", "high" - buffer latency
        """
        if not SOUNDDEVICE_AVAILABLE:
            raise RuntimeError("sounddevice not available")

        self.device_id = device_id
        self.latency = latency
        self.stream = None
        self.current_file = None

        # Latency settings (in seconds)
        self.latency_map = {
            "low": 0.005,      # 5ms - minimal latency
            "medium": 0.02,   # 20ms - balanced
            "high": 0.05,     # 50ms - stable
        }

    def _get_device_info(self) -> dict:
        """Get information about the selected device."""
        if self.device_id is None:
            return {"name": "Default", "latency": self.latency_map.get(self.latency, 0.02)}

        try:
            dev = sd.query_devices(self.device_id)
            return {
                "name": dev["name"],
                "channels": dev["max_output_channels"],
                "sample_rate": dev["default_samplerate"],
                "latency": dev.get("default_low_output_latency", 0.01),
            }
        except:
            return {"name": "Unknown", "latency": 0.02}

    def play_file(
        self,
        file_path: str,
        volume: float = 1.0,
        dsp_filter: str = "",
        output_sample_rate: Optional[int] = None,
    ) -> bool:
        """
        Play an audio file using sounddevice.

        This uses FFmpeg to decode and sounddevice to play.
        For true high-quality output, use the streaming API instead.
        """
        if not Path(file_path).exists():
            log.error(f"File not found: {file_path}")
            return False

        self.stop()

        # Use FFmpeg to decode to raw PCM, then play via sounddevice
        import subprocess

        device_info = self._get_device_info()
        resolved_rate = int(output_sample_rate or device_info.get("sample_rate") or 48000)
        cmd = [
            "ffmpeg", "-i", file_path,
            "-f", "s16le",  # 16-bit signed integer
            "-acodec", "pcm_s16le",
            "-ar", str(resolved_rate),
            "-ac", "2",      # Stereo
            "-"
        ]
        if dsp_filter:
            cmd[2:2] = ["-af", dsp_filter]

        try:
            # Start FFmpeg process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )
            log.info(f"Playing via device: {device_info['name']}")

            # Create output stream
            def callback(outdata, frames, time_info, status):
                if status:
                    pass

                data = process.stdout.read(frames * 4)  # 2 channels * 2 bytes
                if not data:
                    outdata.fill(0)
                    raise sd.CallbackStop()
                outdata[:] = (
                    numpy.frombuffer(data, dtype='int16').reshape(-1, 2) / 32768.0
                ) * float(volume)

            import numpy

            self.stream = sd.OutputStream(
                device=self.device_id,
                samplerate=resolved_rate,
                channels=2,
                callback=callback,
                latency=self.latency_map.get(self.latency, 0.02),
                dtype='float32'
            )

            self.stream.start()
            self.current_file = file_path
            return True

        except Exception as e:
            log.error(f"Playback error: {e}")
            return False

    def stop(self):
        """Stop playback."""
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except:
                pass
            self.stream = None
        self.current_file = None

    def set_volume(self, volume: float):
        """Set playback volume (0.0 to 1.0)."""
        if self.stream:
            # Note: sounddevice doesn't have per-stream volume
            # This would need to be implemented in the callback
            pass

    def get_position(self) -> float:
        """Get current playback position in seconds."""
        # Would need to track this in callback
        return 0.0


# ─── Player Management ───────────────────────────────────────────────────────────

def play_local(
    track_id: int,
    device_id: Optional[int] = None,
    latency: str = "medium",
    volume: float = 1.0,
    dsp_profile_id: Optional[int] = None,
    upsample: str = "none",
) -> dict:
    """
    Play a track locally on the specified audio device.

    Returns:
        {"success": bool, "device": str, "latency": str}
    """
    if not SOUNDDEVICE_AVAILABLE:
        return {"success": False, "error": "sounddevice not available"}

    # Get track file path
    DB_PATH = _get_library_db_path()
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    track = conn.execute(
        "SELECT file_path, sample_rate FROM tracks WHERE id=?", (track_id,)
    ).fetchone()

    dsp_profile = None
    if dsp_profile_id:
        dsp_profile = conn.execute(
            "SELECT * FROM dsp_profiles WHERE id=?", (dsp_profile_id,)
        ).fetchone()
    conn.close()

    if not track:
        return {"success": False, "error": "Track not found"}

    file_path = track["file_path"]
    if not Path(file_path).exists():
        return {"success": False, "error": "File not found on disk"}

    try:
        player = LocalPlayer(device_id=device_id, latency=latency)
        device_info = player._get_device_info()
        output_sample_rate = _resolve_output_sample_rate(
            track["sample_rate"],
            upsample,
            float(device_info.get("sample_rate", 48000) or 48000),
        )
        dsp_filter = build_dsp_filter_chain(dict(dsp_profile)) if dsp_profile else ""
        success = player.play_file(
            file_path,
            volume,
            dsp_filter=dsp_filter,
            output_sample_rate=output_sample_rate,
        )

        if success:
            with _state_lock:
                _playback_state["playing"] = True
                _playback_state["paused"] = False
                _playback_state["track_id"] = track_id

            return {
                "success": True,
                "device": device_info["name"],
                "latency": latency,
                "track_id": track_id,
                "dsp_profile_id": dsp_profile_id,
                "upsample": upsample,
                "output_sample_rate": output_sample_rate,
            }
        return {"success": False, "error": "Playback failed"}

    except Exception as e:
        log.error(f"Local playback error: {e}")
        return {"success": False, "error": str(e)}


def stop_local() -> dict:
    """Stop local playback."""
    # This would need to access the global player instance
    with _state_lock:
        _playback_state["playing"] = False
        _playback_state["paused"] = False
    return {"success": True}


def pause_local() -> dict:
    """Pause local playback."""
    with _state_lock:
        if _playback_state["playing"]:
            _playback_state["paused"] = True
    return {"success": True}


def resume_local() -> dict:
    """Resume local playback."""
    with _state_lock:
        if _playback_state["paused"]:
            _playback_state["paused"] = False
    return {"success": True}


def get_playback_status() -> dict:
    """Get current playback status."""
    with _state_lock:
        return dict(_playback_state)
