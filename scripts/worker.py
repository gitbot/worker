from boto.sqs import connect_to_region as sqsconnect
from boto.sqs.queue import Queue
from fswrap import File, Folder
import json
from multiprocessing import Process, Pipe
import os
import pwd
import requests
from subprocess import check_call
import sys
import yaml

class HandledException(Exception):
    pass

def setup_env(user_name, data):
    os.setuid(pwd.getpwnam(user_name)[2])
    home = Folder('/home').child_folder(user_name)
    os.environ['HOME'] = home.path
    os.environ['BASH_ENV'] = home.child('.profile')
    os.chdir(home.path)
    venv = user_name.replace('gitbot-user-', 'gitbot-env-')
    check_call(['/usr/bin/virtualenv', '--system-site-packages', venv])

    def activate():
        f = home.child_folder(venv).child('bin/activate_this.py')
        execfile(f, dict(__file__=f))

    activate()

    if 'github_oauth' in data:
        check_call(['git', 'config', '--global', 'credential.helper', 'store'])
        credential = 'https://{oauth}:x-oauth-basic@github.com'
        cred_file = home.child_file('.git-credentials')
        cred_file.write(credential.format(oauth=data['github_oauth']))
    return home, activate


def load_actions(home, data):

    source_root = home.child_folder('src')
    source_root.make()
    os.chdir(source_root.path)

    if 'actions_repo' in data:
        actions_repo = data['actions_repo']
    else:
        actions_repo = 'https://github.com/' + data['project'] + '.git'

    check_call(['git', 'clone', '--depth=1', actions_repo, 'actions'])
    source = source_root.child_folder('actions')
    os.chdir(source.path)

    init_file = File(source.child('__init__.py'))
    if not init_file.exists:
        init_file.write('')

    if source.child_file('requirements.txt').exists:
        check_call(['pip', 'install', '-r', 'requirements.txt'])
    if source.child_file('package.json').exists:
        check_call(['npm', 'install'])
    if source.child_file('install.sh').exists:
        check_call(['bash', 'install.sh'])

    sys.path.append(source_root.path)


def xec(user_name, data, parent):

    home, activate = setup_env(user_name, data)


    def finish(status):
        parent.send(status)
        parent.close()
        return

    try:
        load_actions(home, data)
        activate()
    except:
        return finish(
            dict(state='error',
            message='Cannot clone the actions module'))

    try:
        from actions import actions
    except Exception:
        return finish(
            dict(state='error',
            message='Cannot import the actions module'))

    if 'command' in data:
        command_name = data['command']
    else:
        command_name = data['action']['command']

    try:
        command = getattr(actions, command_name)
    except AttributeError:
        command = None
        return finish(
            dict(state='error',
            message='Command [%s] not found' % data['command']))


    parent.send(dict(state='running'))

    try:
        res = command(data)
    except Exception, e:
        return finish(dict(state='failed', message=e.message ))

    result = dict()
    result.update(res)

    finish(dict(state=result.get('state', 'completed'),
                message=result.get('message', ''),
                url=result.get('url', '')))


def run(data):
    user_name = 'gitbot-user-' + data['project'].replace('/', '-')
    check_call(['/usr/sbin/adduser',
                    '--disabled-password',
                    '--gecos', '""', user_name])

    status = None
    status_url = data.get('status_url', None)
    post_status(status_url, dict(state='started'))
    try:
        receiver, sender = Pipe(False)
        p = Process(target=xec, args=(user_name, data, sender))
        p.start()
        while True:
            try:
                status = receiver.recv()
            except EOFError:
                receiver.close()
                break
            else:
                try:
                    result = dict(state='running')
                    result.update(status)
                    post_status(status_url, result)
                    if result['state'] == 'completed' or \
                        result['state'] == 'error' or \
                        result['state'] == 'failed':
                        receiver.close()
                        break
                except:
                    pass
        p.join()
    finally:
        check_call(['/usr/sbin/deluser', '--quiet', '--remove-home', user_name])

    return status

def post_status(status_url, status_data):
    if not status_url:
        return
    headers = {
        "Content-type": "application/json",
        "Accept": "text/plain"
    }
    response = requests.post(status_url,
                    data=json.dumps(status_data),
                    headers=headers)
    if not response.status_code == 200:
        print 'Error: Posting status failed'
        print  response.text



def poll():
    running = File('/var/run/build')
    if running.exists:
        return
    queue_url = '{ "Ref" : "InputQueue" }'
    region = '{ "Ref" : "AWS::Region" }'
    aws_access_key = '{"Ref": "WorkerKeys"}'
    aws_secret_key = '{"Fn::GetAtt": ["WorkerKeys", "SecretAccessKey"]}'
    conn = sqsconnect(region,
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key)
    q = Queue(conn, queue_url)
    msg = q.read(600)
    if msg:
        body = msg.get_body()
        data = json.loads(body)
        status_url = data.get('status_url', None)
        running.write('.')
        try:
            run(data)
            q.delete_message(msg)
        except HandledException:
            raise
        except Exception, e:
            if status_url:
                post_status(status_url,  dict(
                    state='error',
                    message=e.message
                ))
            raise
        running.delete()
        print 'All done.'


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
