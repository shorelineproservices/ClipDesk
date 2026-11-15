"""Clip Desk: GTA 6 launch clips. Dex (director) splits footage, Rico and Jax (clippers) cut vertical clips,
Pia (publisher) posts them on schedule. All of it runs on the laptop, where the footage and ffmpeg are."""
from . import director, clipper, publisher

HANDLERS = {"clip_director": director.run, "clipper": clipper.run, "publisher": publisher.run}
