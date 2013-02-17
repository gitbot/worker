from boto.s3 import connect_to_region as s3connect
from boto.sqs import connect_to_region as sqsconnect
from boto.sqs.queue import Queue
from fswrap import File, Folder
import httplib
import imp
import json
from multiprocessing import Process, Pipe
import os
import pwd
from subprocess import check_call
import sys
import urllib
from urlparse import urlsplit, urlunsplit, ParseResult
import yaml

QUEUE_URL = '{ "Ref" : "InputQueue" }'
REGION = '{ "Ref" : "AWS::Region" }'

def xec(user_name, data, parent):
    uid = pwd.getpwnam(user_name)[2]
    os.setuid(uid)
    home = Folder('/home').child_folder(user_name)
    os.environ['HOME'] = home.path
    os.environ['BASH_ENV'] = home.child('.bashrc')
    os.chdir(home.path)
    venv = user_name.replace('gitbot-user-', 'gitbot-env-')
    check_call(['/usr/bin/virtualenv', venv])
    activate = home.child_folder(venv).child('bin/activate_this.py')
    execfile(activate, dict(__file__=activate))
    setup_keys(user_name, data)
    check_call(['git', 'clone', '--depth=1',
                    '--branch', data['branch'],
                    data['actions_repo'], 'actions'])
    source = Folder('/home/' + user_name + '/actions')
    os.chdir(source.path)
    if source.child_file('requirements.txt').exists:
        check_call(['pip', 'install', '-r', 'requirements.txt'])
    if source.child_file('package.json').exists:
        check_call(['npm', 'install'])
    if source.child_file('install.sh').exists:
        check_call(['bash', 'install.sh'])
    execfile(activate, dict(__file__=activate))
    actions = imp.load_source('actions', source.child('actions.py'))
    try:
        command = getattr(actions, data['command'])
    except AttributeError:
        command = None
        parent.send(
            dict(state='error', 
            message='Command [%s] not found' % data['command']))
    try:
        res = command(data)
        result = dict().update(res)
        parent.send(dict(
            state='complete',
            message=result.get('message', 'Success'),
            url=result.get('url', '')
        ))
    except UserWarning, w:
        parent.send(
            dict(
                state='failure', 
                message=w.message
            ))
    except Exception, e:
        parent.send(
            dict(
                state='error', 
                message=e.message
            ))


def setup_keys(user_name, data):
     if 'github_oauth' in data:
        home = Folder('/home').child_folder(user_name)
        check_call(['git', 'config', 'credential.helper', 'store'])
        credential = 'https://{oauth}:x-oauth-basic@github.com'
        cred_file = home.child_file('.git-credentials')
        cred_file.write(credential.format(oauth=data['github_oauth']))

def run(data):
    user_name = 'gitbot-user-' + data['project'].replace('/', '-')
    check_call(['/usr/sbin/adduser', '--disabled-password', '--gecos', '""', user_name])
    try:
        child, parent = Pipe()
        p = Process(target=xec, args=(user_name, data, parent))
        p.start()
        status = child.recv()
        post_status(status)
        p.join()
    finally:
        check_call(['/usr/sbin/deluser', '--quiet', '--remove-home', user_name])

def post_status(status_url, status_data):
    params = urllib.urlencode(status_data)
    headers = {
        "Content-type": "application/x-www-form-urlencoded",
        "Accept": "text/plain"
    }
    split = urlparse(status_url)
    server = urlunsplit(ParseResult(split.scheme, split.netloc, '', '', ''))
    conn = httplib.HTTPConnection(server)
    conn.request("POST", split.path, params, headers)
    response = conn.getresponse()
    if not response.status == 200:
        raise Exception("Cannot post status")


def poll():
    running = File('/var/run/build')
    if running.exists:
        return
    AWS_ACCESS_KEY = '{"Ref": "WorkerKeys"}'
    AWS_SECRET_KEY = '{"Fn::GetAtt": ["WorkerKeys", "SecretAccessKey"]}'
    conn = sqsconnect(REGION,
                aws_access_key_id=AWS_ACCESS_KEY,
                aws_secret_access_key=AWS_SECRET_KEY)
    q = Queue(conn, QUEUE_URL)
    msg = q.read(600)
    if msg:
        data = json.loads(msg.get_body())
        status_url = data.get('status_url', None)
        running.write('.')
        try:
            run(data)
            q.delete_message(msg)
        except Exception, e:
            # Handle error
            if status_url:
                post_status(status_url,  dict(
                    state='error',
                    message=e.message
                ))
            raise
        finally:
            running.delete()


def test(data_file):
    data = yaml.load(data_file)
    run(data)


def main():
    if len(sys.argv) > 1:
        test(sys.argv[1])
    else:
        poll()

if __name__ == '__main__':
    main()
