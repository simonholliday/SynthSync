import asyncio
import gc
import sys

import microdot
import microdot.websocket

app = microdot.Microdot()
STATE = {'bpm': 120.0, 'running': False}


@app.get('/api/state')
async def state (request):
	return STATE


@app.post('/api/bpm')
async def bpm (request):
	STATE['bpm'] = float(request.json['bpm'])
	return STATE


@app.get('/ws')
@microdot.websocket.with_websocket
async def ws (request, sock):
	while True:
		msg = await sock.receive()
		await sock.send('echo:' + msg)


async def main ():
	port = 8089
	task = asyncio.create_task(app.start_server(host='127.0.0.1', port=port))
	await asyncio.sleep(0.3)
	reader, writer = await asyncio.open_connection('127.0.0.1', port)
	writer.write(b'GET /api/state HTTP/1.0\r\n\r\n')
	await writer.drain()
	data = b''
	while True:
		chunk = await reader.read(500)
		if not chunk:
			break
		data += chunk
	print('GET  ->', data.split(b'\r\n')[0], data.split(b'\r\n\r\n')[1])
	writer.close()
	reader, writer = await asyncio.open_connection('127.0.0.1', port)
	body = b'{"bpm": 133.5}'
	writer.write(b'POST /api/bpm HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n' % len(body) + body)
	await writer.drain()
	data = b''
	while True:
		chunk = await reader.read(500)
		if not chunk:
			break
		data += chunk
	print('POST ->', data.split(b'\r\n')[0], data.split(b'\r\n\r\n')[1])
	writer.close()
	app.shutdown()
	await task
	gc.collect()
	print('ok on', sys.implementation.name, sys.version.split()[0])

asyncio.run(main())
