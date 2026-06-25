Media shown inside the Setup wizard. Files are picked up by filename prefix:

  Step 1 (API key) — files starting with "key":
      key2.png / key2.jpg   → shown under step 2
      key3.png / key3.jpg   → shown under step 3

  Final page (Congratulations) — files starting with "video":
      video.mp4 / video.mov / video.webm   → looping muted video
      video.gif                            → animated GIF (most reliable)

Supported: .png .jpg .jpeg .gif (images) and .mp4 .mov .webm (video).
Images wider than 460px are scaled down. If video playback is unavailable
on a system, export the animation as video.gif instead (always works).
