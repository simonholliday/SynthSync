# What happens to PicoClock's first core when WiFi is poor

Measured on 14 November 2026, with a Pico 2 W joined to a home WiFi network at
a signal between -63 and -80 dBm, and its page used hard from a wired computer
for eleven minutes: two pages open, thirty changes a second, the page's files
fetched and changes sent by POST.

The PicoClock under test was a copy with reporting added. Every two seconds it
printed what its radio said of itself. It printed any send to a page that took
longer than 0.3 s, any gap of more than 0.6 s between turns of its main loop,
and, from a hardware timer, any stretch of two seconds or more in which the
main loop didn't turn at all.

- `what-the-pico-reported.txt` is what it printed over USB, with the time of
  each line.
- `what-the-traffic-saw.txt` is the computer's side: one line for each minute
  of traffic.

## What it shows

Five times in eleven minutes, for between 24 and 97 seconds, the page stopped
answering. In each, a send to an open page took exactly 10 or 20 seconds, and
nothing else on the first core ran meanwhile: the two jobs that report their
own gaps both show the same 10 and 20 seconds. The hardware timer kept ticking
twice a second throughout, so interrupts were never held off. The radio said
all along that it was joined, with an address, and could name the access point.

The clock, on the second core, played on with no timing fault.

## Why

MicroPython's network sockets keep trying for up to ten seconds when the
network stack has no memory to queue what is being sent, and the first core
does nothing else while they try. The stack has 8,000 bytes for everything
waiting to be sent on every connection. On a poor signal a lost packet leaves
data waiting, the memory fills, and each send after that holds the first core
for ten seconds.

## How much was waiting when it went wrong

Measured later the same night, on the same network, with the same traffic for
ten minutes. PicoClock by then sent its messages in numbered batches and each
page answered every batch with its number, so PicoClock could count what it
had sent each page against what the page had said it received. For this run
it only counted, and held nothing back.

- **One page had 28 batches unanswered at once,** and 9,099 bytes were on
  their way to pages together. The network stack has 8,000.
- **Everything but the clock was held up 28 times** for more than a second,
  for 303 seconds of the 600. The longest was 30 seconds.
- **154 of 727 requests went unanswered** within eight seconds, and in 14 of
  40 rounds the listening page ended out of step with the page changing things.
- The clock had no timing fault.

So what piles up on a poor signal is many times what a limit of two batches
and 3,000 bytes allows.

## What holding back did

The run straight after, the same ten minutes of the same traffic, with
PicoClock sending a page no more than two batches beyond the last it had
answered, and never having more than 3,000 bytes on their way to all the
pages together:

| | counting only | holding back |
|---|---|---|
| Times everything but the clock was held up for over a second | 28 | 0 |
| How long that came to | 303 s | 0 s |
| The longest the first core went without a turn | 29.9 s | 0.12 s |
| Requests that went unanswered | 154 of 727 | 0 of 1,782 |
| Rounds that ended with the two pages out of step | 14 of 40 | 0 of 99 |
| Timing faults | 0 | 0 |

**That table alone doesn't show what the limits did,** because the signal
changed through the night. Five more minutes of counting only, half an hour
later, held nothing up either. It still had 19 batches unanswered for one page
and 5,419 bytes on their way at once, which is two thirds of what the network
stack has.

## Stalled on purpose

To take the signal out of it, three pages were made to stall: each asked for
the page's socket and then never read a byte, with room for only a couple of
thousand bytes at its own end, so anything more waited on the Pico. One
ordinary page changed two outputs thirty times a second meanwhile.

| | counting only | holding back |
|---|---|---|
| Times everything but the clock was held up for over a second | 17 | 0 |
| How long that came to | 592 s of 595 | 0 s |
| The longest the first core went without a turn | 39.9 s | 0.02 s |
| The most taken to be on its way to pages at once | 26,923 bytes | under 3,000 |
| The ordinary page's changes that came back to it | none after the first seconds | 1,126 of 1,200 |
| The stalled pages | kept until closed from the other end | all three let go |
| Timing faults | 0 | 0 |

Counting only, the page stopped answering anything for as long as the three
stalled pages stayed open, which was ten minutes, and answered again the
moment they were closed. Holding back, nothing was held up at all.

A new page is first sent an empty batch and is greeted once it has answered
it. Before that was added, each stalled page took the room a greeting needs
until it was let go, eight seconds later, and a phone opening the page behind
three of them waited for all three.

## One page left open

One page open for five minutes on the same network, answering as a page does,
with the clock running and nothing else using the page: 1,173 messages came,
the longest wait between two was 1.8 seconds, six waits were over a second and
none over two. It was never let go, and nothing was held up.

## What is still outside the limits

The page's own files. They are sent whole, a connection each, and three at
once want more than the 8,000 bytes there are. None of them held anything up
in these runs.
