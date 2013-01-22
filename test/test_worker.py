from boto.sqs import connect_to_region
from boto.sqs.queue import Queue
from fswrap import File
from gitbot import stack
from gitbot.lib.s3 import Bucket
import json
from time import sleep
import urllib2
import yaml


HERE = File(__file__).parent

# Create worker stack
config = yaml.load(HERE.parent.child_file('gitbot.yaml').read_all())
publish = yaml.load(HERE.child_file('test.gitbot.yaml').read_all())
config.update(publish)
try:
    stack.publish_stack(config, {}, debug=True)
except:
    pass

#
# Wait for stack to be operational

result = stack.get_outputs(config, wait=True)

print '\n Stack operational. Sending message.\n'

queue_url = result['QueueURL']
access_key = result['ManagerKey']
secret = result['ManagerSecret']

# Send SQS message

data = dict(
    project='gitbot/test',
    actions_repo='git://github.com/gitbot/test.git',
    repo='gitbot/www',
    branch='master',
    bucket='releases.dev.gitbot.test',
    keys=dict(access_key=access_key, secret=secret),
    command='all'
)

conn = connect_to_region('us-east-1',
                aws_access_key_id=access_key,
                aws_secret_access_key=secret)
queue = Queue(conn, queue_url)
message = queue.new_message(json.dumps(data))
message = queue.write(message)

# Wait for result
b = Bucket(
    data['bucket'],
    aws_access_key_id=access_key,
    aws_secret_access_key=secret)
b.connect()

print '\n Wating for bucket:\n'

key = None
key_path = 'result.log'
while not b.bucket:
    print '.'
    sleep(100)
    b.connect()

print '\n Wating for result.log:\n'


def poll_s3():
    try:
        url = b.get_signed_url('result.log')
        response = urllib2.urlopen(url)
    except:
        print '.'
        sleep(100)
        return poll_s3()
    else:
        return response.read()

data = poll_s3()

# Verify result
print data

b.delete(recurse=True)
