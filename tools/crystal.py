"""Measure how fast or slow a Pico's own clock runs, against this computer's.

    python -m tools.crystal
    python -m tools.crystal --minutes 10

Every timing figure the rig gives is against the Pico's own crystal: it says
the pulses are exactly where the Pico thinks they should be. This says whether
the Pico's second is a true second.

The Pico counts time by its crystal and says how far it has got every half
minute, over USB. This computer notes when each report arrives, by its own
clock, which is kept right from the network. A straight line through the
reports gives how much faster or slower the Pico counts, in parts per million.
The delay over USB is the same for every report, so it drops out.

An hour gives the answer to about one part per million. It stops PicoClock
while it runs, since it needs the Pico's prompt, and restarts the Pico at the
end. It needs a Pico running MicroPython on a USB port, and this computer's
clock set from the network.
"""

import argparse
import subprocess
import sys
import time

import tools.install
import tools.stage

# Run on the Pico: count time by its own clock and say how far it has got, in
# microseconds, at the start and every so often after. Its counter of
# microseconds starts again every eighteen minutes, so the count is kept by
# adding up short steps.
COUNT = """
import time

def count (every, reports):
	last = time.ticks_us()
	elapsed = 0
	due = 0
	said = 0
	while said <= reports:
		now = time.ticks_us()
		elapsed += time.ticks_diff(now, last)
		last = now
		if elapsed >= due:
			print("CRYSTAL", elapsed)
			due += every
			said += 1
		time.sleep_ms(20)

count(%d, %d)
"""

# How often the Pico reports, in seconds.
EVERY = 30


def fit (reports: list[tuple[float, int]]) -> tuple[float, float]:

	"""Give how fast the Pico counts against this computer, in parts per million, and how far the reports stray from a straight line, in milliseconds.

	Each report is when it arrived, in seconds by this computer's clock, and
	what the Pico said it had counted, in microseconds. A positive answer
	means the Pico counts fast.
	"""

	count = len(reports)

	if count < 3:
		raise ValueError("At least three reports are needed to measure anything")

	# Taken from the first report, so the sums stay small enough to be exact.
	arrived = [when - reports[0][0] for when, counted in reports]
	counted = [(said - reports[0][1]) / 1000000 for when, said in reports]

	mean_arrived = sum(arrived) / count
	mean_counted = sum(counted) / count
	spread = sum((each - mean_arrived) ** 2 for each in arrived)

	if not spread:
		raise ValueError("The reports all arrived at once")

	slope = sum((arrived[index] - mean_arrived) * (counted[index] - mean_counted) for index in range(count)) / spread
	start = mean_counted - slope * mean_arrived

	stray = (sum((counted[index] - (start + slope * arrived[index])) ** 2 for index in range(count)) / count) ** 0.5

	return (slope - 1) * 1000000, stray * 1000


def listen (device: str, minutes: float, say: bool = True) -> list[tuple[float, int]]:

	"""Have the Pico count for this long, and give back when each of its reports arrived and what it said."""

	wanted = int(minutes * 60 / EVERY)
	command = [tools.stage.find_tool("mpremote") or "mpremote", "connect", device, "soft-reset", "exec", COUNT % (EVERY * 1000000, wanted)]
	reports: list[tuple[float, int]] = []

	with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1) as running:

		assert running.stdout is not None

		for line in running.stdout:

			arrived = time.time()
			words = line.split()

			if len(words) == 2 and words[0] == "CRYSTAL" and words[1].isdigit():

				reports.append((arrived, int(words[1])))

				if say and len(reports) > 2 and (len(reports) - 1) % 10 == 0:
					print("  after %d minutes: %+.1f parts per million" % ((len(reports) - 1) * EVERY // 60, fit(reports)[0]), flush=True)

	return reports


def main (arguments: list[str] | None = None) -> int:

	"""Measure the Pico's clock and say how it compares."""

	parser = argparse.ArgumentParser(description="Measure how fast or slow a Pico's own clock runs, against this computer's.")
	parser.add_argument("--minutes", type=float, default=60.0, help="how long to measure for")
	parser.add_argument("--device", help="the Pico's serial port; found automatically when only one is plugged in")
	options = parser.parse_args(arguments)

	found = tools.install.find_devices()

	if options.device is None and len(found) != 1:
		print("No Pico running MicroPython was found on a USB port." if not found else "More than one Pico is plugged in. Name one with --device.")
		return 1

	device = options.device or found[0]

	print("Measuring the clock of the Pico on %s for %g minutes. PicoClock is stopped meanwhile." % (device, options.minutes), flush=True)

	reports = listen(device, options.minutes)

	tools.install.on_pico(device, ["reset"])

	if len(reports) < 3:
		print("The Pico made %d reports, which is too few to measure anything." % len(reports))
		return 1

	error, stray = fit(reports)
	length = reports[-1][0] - reports[0][0]

	print("Over %.1f minutes and %d reports, the Pico's clock ran %s by %.1f parts per million." % (length / 60, len(reports), "fast" if error >= 0 else "slow", abs(error)))
	print("The reports stray from a straight line by %.2f ms, which is the USB's delay coming and going." % stray)
	print("At 120 BPM that is %.4f BPM, or %.2f seconds in an hour." % (120 * abs(error) / 1000000, 3600 * abs(error) / 1000000))
	print("Restarted the Pico.")

	return 0


if __name__ == "__main__":
	sys.exit(main())
