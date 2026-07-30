"""
Loom — Milestone 1, capture smoke test.

Goal: answer ONE question before I build anything else —
      can I read the screen's pixels at all on this machine?

Wayland deliberately stops apps from reading each other's pixels, so a
failed grab usually comes back as a pure-black image. I capture the
primary monitor, save it to a PNG, and report the average brightness.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import mss
import numpy as np
import cv2


def grab_primary_monitor():
    """Capture the primary monitor and return it as a numpy image (BGR)."""
    with mss.mss() as sct:
        # monitors[0] = all screens combined; monitors[1] = the first real monitor.
        monitor = sct.monitors[1]
        raw = sct.grab(monitor)

        # raw is an mss ScreenShot. np.array() turns its pixel buffer into a
        # height x width x 4 array in BGRA order (blue, green, red, alpha).
        bgra = np.array(raw)

        # OpenCV works in BGR (no alpha). Drop the alpha channel.
        bgr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
        return bgr, monitor


def main():
    image, monitor = grab_primary_monitor()

    height, width = image.shape[:2]
    mean_brightness = float(image.mean())

    out_path = "smoketest.png"
    cv2.imwrite(out_path, image)

    print(f"Captured monitor geometry: {monitor}")
    print(f"Image size: {width} x {height}")
    print(f"Mean brightness (0=black, 255=white): {mean_brightness:.1f}")

    if mean_brightness < 1.0:
        print("RESULT: looks BLACK — Wayland is probably blocking capture.")
    else:
        print("RESULT: got a real image — capture works. Open smoketest.png to confirm.")


if __name__ == "__main__":
    main()
