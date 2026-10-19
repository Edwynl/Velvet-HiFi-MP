"""
MusicIQ Local Playback Module
────────────────────────────
Native audio output through the host APIs exposed by sounddevice/PortAudio.

Requirements:
  • sounddevice: pip install sounddevice
  • For ASIO support: Install ASIO drivers for your audio interface

The selected device uses its default stream settings. This module does not
request WASAPI exclusive mode or negotiate ASIO/DSD capabilities.
"""

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import numpy

import env_loader  # noqa: F401 - load .env for direct module use
from dsp_engine import build_dsp_filter_chain, dsp_effective_status
from path_compat import filesystem_path

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
    SOUNDDEVICE_ERROR = None
except (ImportError, OSError) as exc:
    # OSError: the wheel is present but the PortAudio library is not
    # (e.g. Linux without libportaudio2). Report it instead of crashing.
    SOUNDDEVICE_AVAILABLE = False
    SOUNDDEVICE_ERROR = f"{type(exc).__name__}: {exc}"
    print(f"WARNING: sounddevice unavailable ({SOUNDDEVICE_ERROR}). Local playback disabled.")
    print("Install with: pip install sounddevice")

log = __import__("logging").getLogger("velvet.playback")

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
_player_lock = threading.Lock()
# Orders a natural end-of-track against stop()/a new track on the same player.
_player_state_guard = threading.Lock()
_active_player: Optional["LocalPlayer"] = None

FRAME_BYTES = 8                # stereo float32
DECODE_CHUNK_FRAMES = 4096
RING_BUFFER_SECONDS = 2.0
PREBUFFER_SECONDS = 0.25
PREBUFFER_TIMEOUT_SECONDS = 2.0

UPSAMPLE_MULTIPLIERS = {
    "none": 1,
    "2x": 2,
    "4x": 4,
    "8x": 8,
    "16x": 16,
}


def _get_library_db_path() -> Path:
    """Resolve the active library database path for this project."""
    data_dir = os.environ.get("VELVET_DATA_DIR") or os.environ.get("DATA_DIR")
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


class PcmRingBuffer:
    """Bounded single-producer/single-consumer float32 frame FIFO.

    The decoder thread blocks in ``write`` while the buffer is full; the
    PortAudio callback uses ``read_into``, which never waits on I/O or on the
    producer — it copies whatever is available and reports the count.
    """

    def __init__(self, capacity_frames: int, channels: int = 2):
        if capacity_frames <= 0:
            raise ValueError("capacity_frames must be positive")
        self._buffer = numpy.zeros((capacity_frames, channels), dtype="float32")
        self.capacity = capacity_frames
        self._start = 0
        self._count = 0
        self._eof = False
        self._closed = False
        self._lock = threading.Lock()
        self._space = threading.Condition(self._lock)

    @property
    def available(self) -> int:
        return self._count

    @property
    def eof(self) -> bool:
        return self._eof

    @property
    def drained(self) -> bool:
        """True once the producer finished and every frame was consumed."""
        return self._eof and self._count == 0

    def write(self, frames) -> bool:
        """Append frames, waiting for space. Returns False once closed."""
        offset = 0
        total = len(frames)
        while offset < total:
            with self._space:
                while self._count == self.capacity and not self._closed:
                    self._space.wait(0.25)
                if self._closed:
                    return False
                end = (self._start + self._count) % self.capacity
                n = min(total - offset, self.capacity - self._count, self.capacity - end)
                self._buffer[end:end + n] = frames[offset:offset + n]
                self._count += n
            offset += n
        return True

    def read_into(self, out, gain: float = 1.0) -> int:
        """Copy up to len(out) frames into ``out`` (scaled by gain); never blocks on I/O."""
        with self._lock:
            n = min(len(out), self._count)
            first = min(n, self.capacity - self._start)
            out[:first] = self._buffer[self._start:self._start + first]
            if n > first:
                out[first:n] = self._buffer[:n - first]
            self._start = (self._start + n) % self.capacity
            self._count -= n
            self._space.notify()
        if gain != 1.0 and n:
            out[:n] *= gain
        return n

    def mark_eof(self):
        with self._lock:
            self._eof = True

    def close(self):
        """Wake and stop a producer blocked on a full buffer."""
        with self._space:
            self._closed = True
            self._space.notify_all()


def _resolve_output_sample_rate(source_rate: Optional[float], upsample: str, device_rate: float) -> int:
    base_rate = int(source_rate or device_rate or 48000)
    if upsample not in UPSAMPLE_MULTIPLIERS:
        raise ValueError("Unsupported upsampling multiplier")
    rate = base_rate * UPSAMPLE_MULTIPLIERS[upsample]
    if not 8000 <= rate <= 768000:
        raise ValueError("Requested PCM output sample rate is unsupported")
    return rate


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
        self.process = None
        self.current_file = None
        # Which library track this player is playing, so a re-issue for the
        # same track can resume instead of starting over.
        self.track_id: Optional[int] = None
        self.output_sample_rate = None
        self._played_frames = 0
        self._gain = 1.0  # Unity gain leaves the PCM samples unchanged.
        # Plain counters written only by the audio callback.
        self.xrun_flags = 0     # callbacks where PortAudio reported a status flag
        self.starvations = 0    # callbacks the ring could not fill before EOF
        self._mme_low_latency_logged = False
        self._ring: Optional[PcmRingBuffer] = None
        self._stop_event: Optional[threading.Event] = None
        self._decoder: Optional[threading.Thread] = None

        # Latency settings (in seconds)
        self.latency_map = {
            "low": 0.005,      # 5ms - minimal latency
            "medium": 0.02,   # 20ms - balanced
            "high": 0.05,     # 50ms - stable
        }

    def _get_device_info(self) -> dict:
        """Get information about the selected device."""
        try:
            dev = sd.query_devices(self.device_id, kind="output")
            return {
                "name": dev["name"],
                "channels": dev["max_output_channels"],
                "sample_rate": dev["default_samplerate"],
                "latency": dev.get("default_low_output_latency", 0.01),
                "host_api": sd.query_hostapis(dev["hostapi"])["name"],
            }
        except:
            return {"name": "Unknown", "latency": 0.02}

    def _stream_latency(self, host_api: Optional[str]) -> float:
        """Requested output latency in seconds for the device's host API."""
        if self.latency == "low" and host_api == "MME":
            # MME consumes far too slowly at 5 ms (measured on Realtek/MME).
            if not self._mme_low_latency_logged:
                self._mme_low_latency_logged = True
                log.info("MME host API: using 'medium' latency for 'low'")
            return self.latency_map["medium"]
        return self.latency_map.get(self.latency, 0.02)

    def play_file(
        self,
        file_path: str,
        volume: float = 1.0,
        dsp_filter: str = "",
        output_sample_rate: Optional[int] = None,
        allow_device_rate_fallback: bool = True,
        start_seconds: float = 0.0,
    ) -> bool:
        """
        Play an audio file using sounddevice.

        This uses FFmpeg to decode and sounddevice to play.
        For true high-quality output, use the streaming API instead.

        start_seconds drops the leading audio instead of playing it. It exists
        so re-issuing playback for the same track (a DSP change mid-track) can
        resume where the old one was rather than starting over.
        """
        readable_path = filesystem_path(file_path)
        if not Path(readable_path).exists():
            log.error(f"File not found: {file_path}")
            return False

        gain = float(volume)
        if not numpy.isfinite(gain) or not 0.0 <= gain <= 1.0:
            raise ValueError("Volume must be a finite number between 0 and 1")
        try:
            offset = float(start_seconds or 0.0)
        except (TypeError, ValueError):
            offset = 0.0
        if not numpy.isfinite(offset) or offset <= 0:
            offset = 0.0
        self.stop()
        self._played_frames = 0
        self._gain = gain

        device_info = self._get_device_info()
        resolved_rate = int(output_sample_rate or device_info.get("sample_rate") or 48000)
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error"]
        if offset > 0:
            # Input-side -ss: ffmpeg seeks and still decodes accurately to the
            # requested timestamp because the output is re-encoded, and it does
            # not have to demux the whole file first.
            cmd.extend(["-ss", f"{offset:.3f}"])
        cmd.extend(["-i", readable_path])
        if dsp_filter:
            # Apply filters after the input and before output encoding.
            cmd.extend(["-af", dsp_filter])
        cmd.extend([
            "-f", "f32le",             # Preserve 24-bit PCM precision in float32
            "-acodec", "pcm_f32le",   # float32 output for sounddevice
            "-ar", str(resolved_rate),
            "-ac", "2",
            "pipe:1",
        ])

        try:
            try:
                sd.check_output_settings(device=self.device_id, samplerate=resolved_rate,
                                         channels=2, dtype="float32")
            except sd.PortAudioError:
                # Shared-mode WASAPI may accept only the Windows mixer rate.
                # Source-rate playback can use that rate, while an explicit
                # upsampling request must fail if the device cannot accept it.
                fallback_rate = int(device_info.get("sample_rate") or 0)
                if not allow_device_rate_fallback or not fallback_rate or fallback_rate == resolved_rate:
                    raise
                sd.check_output_settings(device=self.device_id, samplerate=fallback_rate,
                                         channels=2, dtype="float32")
                log.warning("Device rejected %s Hz; using its shared output rate %s Hz",
                            resolved_rate, fallback_rate)
                resolved_rate = fallback_rate
                cmd[cmd.index("-ar") + 1] = str(resolved_rate)
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )
            log.info(f"Playing via device: {device_info['name']}")

            ring = PcmRingBuffer(max(int(resolved_rate * RING_BUFFER_SECONDS), DECODE_CHUNK_FRAMES))
            stop_event = threading.Event()
            drained = threading.Event()
            self._ring, self._stop_event = ring, stop_event
            self._decoder = threading.Thread(
                target=self._decode_worker,
                args=(self.process, ring, stop_event, drained),
                name="velvet-ffmpeg-decoder",
                daemon=True,
            )
            self._decoder.start()
            # PortAudio real-time thread: memory copy only. Pipe reads, process
            # control, logging and playback-state updates happen elsewhere.
            def callback(outdata, frames, time_info, status):
                if status:
                    self.xrun_flags += 1
                copied = ring.read_into(outdata, self._gain)
                self._played_frames += copied
                if copied < frames:
                    outdata[copied:] = 0
                    if ring.drained:
                        drained.set()
                        raise sd.CallbackStop()
                    if not ring.eof:
                        self.starvations += 1

            # Let the decoder get ahead so the first callbacks do not underrun.
            target = min(ring.capacity, int(resolved_rate * PREBUFFER_SECONDS))
            deadline = time.monotonic() + PREBUFFER_TIMEOUT_SECONDS
            while ring.available < target and not ring.eof and time.monotonic() < deadline:
                time.sleep(0.005)

            self.stream = sd.OutputStream(
                device=self.device_id,
                samplerate=resolved_rate,
                channels=2,
                callback=callback,
                latency=self._stream_latency(device_info.get("host_api")),
                dtype='float32'
            )

            self.current_file = file_path
            self.output_sample_rate = resolved_rate
            self.stream.start()
            return True

        except Exception as e:
            self.stop()
            log.error(f"Playback error: {e}")
            return False

    def _decode_worker(self, process, ring: PcmRingBuffer, stop_event: threading.Event,
                       drained: threading.Event):
        """Read FFmpeg PCM into the ring buffer; finish playback after drain."""
        pending = b""
        try:
            while not stop_event.is_set():
                data = process.stdout.read(DECODE_CHUNK_FRAMES * FRAME_BYTES)
                if not data:
                    break
                data = pending + data
                usable = len(data) - len(data) % FRAME_BYTES
                pending = data[usable:]
                if usable:
                    frames = numpy.frombuffer(data[:usable], dtype="<f4").reshape(-1, 2)
                    if not ring.write(frames):
                        break
        except (OSError, ValueError):
            pass  # stop() closed the pipe
        finally:
            ring.mark_eof()

        # Wait for the callback to play out the tail, then finish here rather
        # than in the PortAudio thread.
        while not stop_event.is_set():
            if drained.wait(0.1):
                self._finish_playback(process, stop_event)
                return

    def _finish_playback(self, process, stop_event: threading.Event):
        """Natural end of track: reap the decoder and clear playback state."""
        try:
            process.wait(timeout=2)
        except Exception:
            pass
        with _player_state_guard:
            if self._stop_event is not stop_event or stop_event.is_set():
                return  # a newer track or an explicit stop owns the state
            # No live generation: play_local must not mark this track playing.
            self._stop_event = None
            with _state_lock:
                _playback_state["playing"] = False
                _playback_state["paused"] = False
                _playback_state["position"] = 0
                _playback_state["track_id"] = None

    def _terminate_process(self):
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)
        except Exception:
            pass
        finally:
            try:
                if process.stdout:
                    process.stdout.close()
            except Exception:
                pass

    def stop(self):
        """Stop playback."""
        with _player_state_guard:
            stop_event, self._stop_event = self._stop_event, None
        if stop_event is not None:
            stop_event.set()
        if self._ring is not None:
            self._ring.close()  # wake a decoder blocked on a full buffer
        # Close the pipe so a decoder blocked in read() returns.
        self._terminate_process()
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except:
                pass
            self.stream = None
        decoder, self._decoder = self._decoder, None
        if decoder is not None and decoder is not threading.current_thread():
            decoder.join(timeout=2)
        self._ring = None
        self.current_file = None
        self.output_sample_rate = None
        self._played_frames = 0

    def pause(self) -> bool:
        """Pause playback without destroying the decoder process."""
        if not self.stream:
            return False
        try:
            self.stream.stop()
            return True
        except Exception as exc:
            log.warning(f"Failed to pause local playback: {exc}")
            return False

    def resume(self) -> bool:
        """Resume playback after pause."""
        if not self.stream:
            return False
        try:
            self.stream.start()
            return True
        except Exception as exc:
            log.warning(f"Failed to resume local playback: {exc}")
            return False

    def set_volume(self, volume: float) -> bool:
        """Adjust digital gain in the PCM callback; unity is untouched.

        Digital attenuation is not bit-perfect. This does NOT control the
        physical DAC amplifier or request WASAPI exclusive mode.
        """
        gain = float(volume)
        if not numpy.isfinite(gain) or not 0.0 <= gain <= 1.0:
            raise ValueError("Volume must be a finite number between 0 and 1")
        if self.stream is None:
            return False
        self._gain = gain
        return True

    def get_position(self) -> float:
        """Approximate PCM progress from frames actually dequeued by PortAudio."""
        rate = self.output_sample_rate or 0
        return round(self._played_frames / rate, 3) if rate else 0.0


# ─── Player Management ───────────────────────────────────────────────────────────

def play_local(
    track_id: int,
    device_id: Optional[int] = None,
    latency: str = "medium",
    volume: float = 1.0,
    dsp_profile_id: Optional[int] = None,
    upsample: str = "none",
    start_seconds: float = 0.0,
    resume: bool = False,
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
    if dsp_profile_id and not dsp_profile:
        # Fail closed: never play unprocessed audio for a requested preset.
        return {"success": False, "error": "DSP profile not found"}

    file_path = track["file_path"]
    if not Path(filesystem_path(file_path)).exists():
        return {"success": False, "error": "File not found on disk"}

    try:
        global _active_player
        with _player_lock:
            if (
                _active_player is None
                or _active_player.device_id != device_id
                or _active_player.latency != latency
            ):
                if _active_player is not None:
                    _active_player.stop()
                _active_player = LocalPlayer(device_id=device_id, latency=latency)
            player = _active_player

            device_info = player._get_device_info()
            output_sample_rate = _resolve_output_sample_rate(
                track["sample_rate"],
                upsample,
                float(device_info.get("sample_rate", 48000) or 48000),
            )
            dsp_filter = (build_dsp_filter_chain(dict(dsp_profile), track["sample_rate"])
                          if dsp_profile else "")
            # Changing the DSP re-issues playback for the track that is already
            # running. The player knows how far it got, so resume here rather
            # than snapping the listener back to the first second.
            offset = float(start_seconds or 0.0)
            if resume and offset <= 0 and player.track_id == track_id:
                offset = max(0.0, float(player.get_position() or 0.0))
            success = player.play_file(
                file_path,
                volume,
                dsp_filter=dsp_filter,
                output_sample_rate=output_sample_rate,
                allow_device_rate_fallback=upsample == "none",
                start_seconds=offset,
            )
            if success:
                player.track_id = track_id

            if success:
                # A 0-frame or failing track can drain before this point;
                # _finish_playback then already cleared the state.
                with _player_state_guard, _state_lock:
                    if player._stop_event is not None:
                        _playback_state["playing"] = True
                        _playback_state["paused"] = False
                        _playback_state["track_id"] = track_id
                        _playback_state["position"] = 0
                        _playback_state["device"] = device_info["name"]
                        _playback_state["volume"] = volume

                return {
                    "success": True,
                    "device": device_info["name"],
                    "latency": latency,
                    "track_id": track_id,
                    # Where playback actually began, so the client does not paint
                    # 0:00 over a track that resumed three minutes in.
                    "start_offset": round(offset, 3),
                    "dsp_profile_id": dsp_profile_id,
                    "dsp": dsp_effective_status(
                        dsp_profile_id, dict(dsp_profile) if dsp_profile else None,
                        track["sample_rate"]),
                    "upsample": upsample,
                    "output_sample_rate": player.output_sample_rate,
                    "requested_output_sample_rate": output_sample_rate,
                }
            return {"success": False, "error": "Playback failed"}

    except Exception as e:
        log.error(f"Local playback error: {e}")
        return {"success": False, "error": str(e)}


def set_local_volume(volume: float) -> dict:
    """Set digital PCM gain for native zone; does not modify Windows/DAC volume."""
    with _player_lock:
        if _active_player is None:
            return {"success": False, "error": "Native playback is not active"}
        try:
            success = _active_player.set_volume(float(volume))
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": str(exc)}
    if success:
        with _state_lock:
            _playback_state["volume"] = float(volume)
    return {"success": success, "volume": float(volume), "digital_gain": True}


def stop_local() -> dict:
    """Stop local playback."""
    global _active_player
    with _player_lock:
        if _active_player is not None:
            _active_player.stop()
    with _state_lock:
        _playback_state["playing"] = False
        _playback_state["paused"] = False
        _playback_state["track_id"] = None
        _playback_state["position"] = 0
    return {"success": True}


def pause_local() -> dict:
    """Pause local playback."""
    global _active_player
    paused = False
    with _player_lock:
        if _active_player is not None:
            paused = _active_player.pause()
    with _state_lock:
        if paused and _playback_state["playing"]:
            _playback_state["paused"] = True
    return {"success": paused}


def resume_local() -> dict:
    """Resume local playback."""
    global _active_player
    resumed = False
    with _player_lock:
        if _active_player is not None:
            resumed = _active_player.resume()
    with _state_lock:
        if resumed and _playback_state["paused"]:
            _playback_state["paused"] = False
    return {"success": resumed}


def get_playback_status() -> dict:
    """Return decoder-consumed PCM position (not a fabricated zero)."""
    with _player_lock:
        position = _active_player.get_position() if _active_player is not None else 0.0
        with _state_lock:
            status = dict(_playback_state)
            status["position"] = position if status.get("track_id") is not None else 0.0
            return status
