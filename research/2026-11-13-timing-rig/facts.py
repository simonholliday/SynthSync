import gc, machine, network, rp2, time, os
gc.collect(); print('heap free at start:', gc.mem_free())
print('Pin ALT consts:', [a for a in dir(machine.Pin) if a.startswith('ALT')])
claimed = []
@rp2.asm_pio()
def nop_prog():
    nop()
for i in range(12):
    try:
        sm = rp2.StateMachine(i, nop_prog)
    except Exception as e:
        claimed.append((i, str(e)))
print('state machines unavailable before wifi:', claimed)
try:
    network.hostname('picoclock')
    print('hostname set:', network.hostname())
except Exception as e:
    print('hostname error', e)
ap = network.WLAN(network.AP_IF)
ap.config(ssid='PicoClock-RIG', password='rigtest123')
t = time.ticks_ms(); ap.active(True)
while not ap.active(): time.sleep_ms(10)
print('AP up in %d ms, ifconfig=%s' % (time.ticks_diff(time.ticks_ms(), t), ap.ifconfig()))
for k in ('ssid', 'channel', 'security', 'mac'):
    try:
        v = ap.config(k)
        print('  ap.config(%s) =' % k, v if k != 'mac' else 'present')
    except Exception as e:
        print('  ap.config(%s) error' % k, e)
gc.collect(); print('heap free with AP up:', gc.mem_free())
claimed = []
for i in range(12):
    try:
        sm = rp2.StateMachine(i, nop_prog)
    except Exception as e:
        claimed.append((i, str(e)))
print('state machines unavailable with wifi up:', claimed)
import bluetooth
ble = bluetooth.BLE(); ble.active(True)
gc.collect(); print('heap free with AP + BLE up:', gc.mem_free())
ble.active(False); ap.active(False)
d = [rp2.DMA() for _ in range(3)]
print('DMA channels handed out:', d)
print('unique id bytes:', len(machine.unique_id()))
print('files on flash:', os.listdir('/'))
