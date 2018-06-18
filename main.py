# Linked: 12059001732
from string import Template
import json
import os
import requests
import struct
import StringIO
import uuid

from tornado import httpserver, httpclient, ioloop, web, websocket, gen
from xml.etree import ElementTree
import nexmo

from azure_auth_client import AzureAuthClient
from config import HOSTNAME, CALLER, LANGUAGE1, VOICE1, LANGUAGE2, VOICE2
from secrets import NEXMO_APPLICATION_ID, NEXMO_PRIVATE_KEY, MICROSOFT_TRANSLATION_SPEECH_CLIENT_SECRET, NEXMO_NUMBER


nexmo_client = nexmo.Client(
    application_id=NEXMO_APPLICATION_ID, private_key=NEXMO_PRIVATE_KEY)
azure_auth_client = AzureAuthClient(MICROSOFT_TRANSLATION_SPEECH_CLIENT_SECRET)

conversation_id_by_phone_number = {}
call_id_by_conversation_id = {}
callerList = {}


class Caller:
    def __init__(self, phone, conv_uuid):
        self.phone = phone
        self.id = str(uuid.uuid4())
        self.language = getCallerLanguage(phone)
        self.conversation_uuid = conv_uuid


class CallHandler(web.RequestHandler):
    @web.asynchronous
    def get(self):
        data = {}
        data['hostname'] = HOSTNAME
        data['whoami'] = self.get_query_argument('from')
        data['cid'] = self.get_query_argument('conversation_uuid')
        conversation_id_by_phone_number[self.get_query_argument('from')] = self.get_query_argument('conversation_uuid')
        getCaller(data['whoami'], data['cid'])
        filein = open('ncco.json')
        src = Template(filein.read())
        filein.close()
        ncco = json.loads(src.substitute(data))
        self.write(json.dumps(ncco))
        self.set_header("Content-Type", 'application/json; charset="utf-8"')
        self.finish()


class EventHandler(web.RequestHandler):
    @web.asynchronous
    def post(self):
        body = json.loads(self.request.body)
        if 'direction' in body and body['direction'] == 'inbound':
            if 'uuid' in body and 'conversation_uuid' in body:
                call_id_by_conversation_id[body['conversation_uuid']
                                           ] = body['uuid']
        self.content_type = 'text/plain'
        self.write('ok')
        self.finish()


class WSHandler(websocket.WebSocketHandler):
    whoami = None

    def open(self):
        print("Websocket Call Connected")
        print(self)

    def translator_future(self, translate_from, translate_to):
        uri = "wss://dev.microsofttranslator.com/speech/translate?from={0}&to={1}&api-version=1.0".format(
            translate_from[:2], translate_to)
        request = httpclient.HTTPRequest(uri, headers={
            'Authorization': 'Bearer ' + azure_auth_client.get_access_token(),
        })
        return websocket.websocket_connect(request, on_message_callback=self.speech_to_translation_completed)

    def speech_to_translation_completed(self, new_message):
        if new_message == None:
            print("Got None Message")
            return
        msg = json.loads(new_message)
        if msg['translation'] != '':
            print("Translated: " + "'" +
                  msg['recognition'] + "' -> '" + msg['translation'] + "'")
            for key, value in conversation_id_by_phone_number.iteritems():
                if key != self.whoami and value != None:
                    if self.whoami == CALLER:
                        speak(
                            call_id_by_conversation_id[value], msg['translation'], VOICE2)
                    else:
                        speak(
                            call_id_by_conversation_id[value], msg['translation'], VOICE1)

    @gen.coroutine
    def on_message(self, message):
        if type(message) == str:
            ws = yield self.ws_future
            ws.write_message(message, binary=True)
        else:
            message = json.loads(message)
            self.whoami = message['whoami']
            print("Sending wav header")
            messageCarrier = getCaller(message['whoami'], message['cid'])
            
            for key, value in callerList.iteritems():
                print("************************************************************")
                print(value.conversation_uuid)
                print(messageCarrier.conversation_uuid)
                print("-------------------------------------------------------------")
                print(messageCarrier.phone)
                print(value.phone)
                print("************************************************************")
                # if value.conversation_uuid == messageCarrier.conversation_uuid and value.phone != messageCarrier.phone:
                if value.phone != messageCarrier.phone:
                    print("HERE!")
                    print("Message receiver properly set with conversation uuid: " + value.conversation_uuid)
                    messageReceiver = value
                    break
                else:
                    print("No message receiver properly set")
                    messageReceiver = Caller("xxxxxxxxxx", "x")

            header = make_wave_header(16000)
            
            print("Where languages are set")
            print(messageCarrier.language)
            print(messageReceiver.language)
            
            self.ws_future = self.translator_future(messageCarrier.language, messageReceiver.language)

            ws = yield self.ws_future
            ws.write_message(header, binary=True)

    @gen.coroutine
    def on_close(self):
        print("Websocket Call Disconnected")


def make_wave_header(frame_rate):
    """
    Generate WAV header that precedes actual audio data sent to the speech translation service.
    :param frame_rate: Sampling frequency (8000 for 8kHz or 16000 for 16kHz).
    :return: binary string
    """

    if frame_rate not in [8000, 16000]:
        raise ValueError(
            "Sampling frequency, frame_rate, should be 8000 or 16000.")

    nchannels = 1
    bytes_per_sample = 2

    output = StringIO.StringIO()
    output.write('RIFF')
    output.write(struct.pack('<L', 0))
    output.write('WAVE')
    output.write('fmt ')
    output.write(struct.pack('<L', 18))
    output.write(struct.pack('<H', 0x0001))
    output.write(struct.pack('<H', nchannels))
    output.write(struct.pack('<L', frame_rate))
    output.write(struct.pack('<L', frame_rate * nchannels * bytes_per_sample))
    output.write(struct.pack('<H', nchannels * bytes_per_sample))
    output.write(struct.pack('<H', bytes_per_sample * 8))
    output.write(struct.pack('<H', 0))
    output.write('data')
    output.write(struct.pack('<L', 0))

    data = output.getvalue()
    output.close()

    return data

# IF THE CALLER EXISTS RETURN THAT CALLER OBJECT
# IF NOT CALLER EXISTS RETURN NEW CALLER OBJECT.


def getCaller(phone, conv_uuid):

    for key, value in callerList.iteritems():
        if value.phone == phone:
            print("Matching caller found.")
            return value
    
    callerList[phone] = Caller(phone, conv_uuid)
    print("Matching caller not found. CREATING NEW CALLER")
    return callerList[phone]



def getCallerLanguage(phone):
    url = "http://translationdemo-fbna.cloudhub.io/language-pref?phone=" + phone
    r = requests.get(url)
    print("Mulesoft provided caller language: " + r.json().get("language"))
    # If no language return value:
    return r.json().get("language")


def speak(uuid, text, vn):
    print("speaking to: " + uuid + " " + text)
    response = nexmo_client.send_speech(uuid, text=text, voice_name=vn)
    print(response)


def main():
    application = web.Application([
        (r"/event", EventHandler),
        (r"/ncco", CallHandler),
        (r"/socket", WSHandler),
    ])

    http_server = httpserver.HTTPServer(application)
    port = int(os.environ.get("PORT: ", 5000))
    http_server.listen(port)
    print("Running on port:: " + str(port))
    ioloop.IOLoop.instance().start()


if __name__ == "__main__":
    main()
