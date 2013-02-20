from boto.s3 import connect_to_region as s3connect
from boto.sqs import connect_to_region as sqsconnect
from boto.sqs.queue import Queue
from fswrap import File, Folder
import httplib
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
    check_call(['/usr/bin/virtualenv', '--system-site-packages', venv])
    activate = home.child_folder(venv).child('bin/activate_this.py')
    execfile(activate, dict(__file__=activate))
    setup_keys(user_name, data)
    source_root = home.child_folder('src')
    source_root.make()
    os.chdir(source_root.path)
    if 'actions_repo' in data:
        actions_repo = data['actions_repo']
    else:
        actions_repo = 'https://github.com/' + data['project'] + '.git'
    check_call(['git', 'clone', '--depth=1',
                    '--branch', data['branch'],
                    actions_repo, 'actions'])
    source = source_root.child_folder('actions')
    os.chdir(source.path)
    if source.child_file('requirements.txt').exists:
        check_call(['pip', 'install', '-r', 'requirements.txt'])
    if source.child_file('package.json').exists:
        check_call(['npm', 'install'])
    if source.child_file('install.sh').exists:
        check_call(['bash', 'install.sh'])
    execfile(activate, dict(__file__=activate))
    init_file = File(source.child('__init__.py'))
    if not init_file.exists:
        init_file.write('')
    sys.path.append(source_root.path)
    try:
        from actions import actions
    except Exception:
        parent.send(
            dict(state='error', 
            message='Cannot import the actions module'))
        raise
    if 'command' in data:
        command_name = data['command']
    else:
        command_name = data['action']['command']
    try:
        command = getattr(actions, command_name)
    except AttributeError:
        command = None
        parent.send(
            dict(state='error', 
            message='Command [%s] not found' % data['command']))
        raise
    try:
        res = command(data)
        result = dict()
        result.update(res)
        parent.send(dict(
            state='complete',
            message=result.get('message', 'Gitbot:: Build completed successfully.'),
            url=result.get('url', '')
        ))
    except UserWarning, w:
        parent.send(
            dict(
                state='failure', 
                message='Gitbot:: Build failed.[%s]' % w.message 
            ))
        raise
    except Exception, e:
        parent.send(
            dict(
                state='error', 
                message='Gitbot:: System error.[%s]' % e.message 
            ))
        raise


def setup_keys(user_name, data):
     if 'github_oauth' in data:
        home = Folder('/home').child_folder(user_name)
        check_call(['git', 'config', '--global', 'credential.helper', 'store'])
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
        post_status(data.get('status_url', None), status)
        p.join()
    finally:
        check_call(['/usr/sbin/deluser', '--quiet', '--remove-home', user_name])

def post_status(status_url, status_data):
    if not status_url:
        return
    params = urllib.urlencode(status_data)
    headers = {
        "Content-type": "application/x-www-form-urlencoded",
        "Accept": "text/plain"
    }
    split = urlsplit(status_url)
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
        body = msg.get_body()
        data = json.loads(body)
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
    data = yaml.load(File(data_file).read_all())
    run(data)


def main():
    if len(sys.argv) > 1:
        test(sys.argv[1])
    else:
        poll()

if __name__ == '__main__':
    main()
