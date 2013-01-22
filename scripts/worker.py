from boto.s3 import connect_to_region as s3connect
from boto.sqs import connect_to_region as sqsconnect
from boto.sqs.queue import Queue
from fswrap import File, Folder
import imp
import json
from multiprocessing import Process
import os
import pwd
from subprocess import check_call
import sys
import yaml

QUEUE_URL = '{ "Ref" : "InputQueue" }'
REGION = '{ "Ref" : "AWS::Region" }'


def xec(user_name, data):
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
        # Raise exception
    if command:
        command(data)



def setup_keys(user_name, data):
    if not 'keys' in data or \
        not 'archive' in data['keys'] or \
        not data['keys']['archive']:
        return

    home = Folder('/home').child_folder(user_name)
    access_key = data['keys']['access_key']
    secret = data['keys']['secret']
    conn = s3connect(REGION,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret)
    b = conn.get_bucket(data['keys']['bucket'])
    key_file = home.child_folder('tmp').child(data['keys']['archive'] or 'keys.zip')
    key_file = File(key_file)
    target = key_file.parent.child_folder('keys')
    k = b.get_key(key_file.name)
    k.get_contents_to_file_name(key_file.path)
    check_call(['unzip', '-jqqd', target.path, key_file.path])
    keys_file = File(target.child('keys.yaml'))
    if not keys_file:
        return
    keys_text = File(target.child('keys.yaml')).read_all()
    keys = yaml.load(keys_text)
    ssh = home.child_folder('.ssh')
    ssh.make()
    domains = False
    if keys.domains:
        domains = ' '.join(keys.domains)
    if keys.conf:
        conf = File(target.child(keys.conf)).copy_to(ssh)
        check_call(['chmod', '000700', conf.path])
    if keys.list:
        for keydata in keys.list:
            pub = File(target.child(keydata['public'])).copy_to(ssh)
            pri = File(target.child(keydata['private'])).copy_to(ssh)
            check_call(['chmod', '000644', pub.path])
            check_call(['chmod', '000600', pri.path])
    if domains:
        check_call(['ssh-keyscan', domains, '>>', ssh.child('known_hosts')])


def run(data):
    user_name = 'gitbot-user-' + data['project'].replace('/', '-')
    check_call(['/usr/sbin/adduser', '--disabled-password', '--gecos', '""', user_name])
    try:
        p = Process(target=xec, args=(user_name, data))
        p.start()
        p.join()
    finally:
        check_call(['/usr/sbin/deluser', '--quiet', '--remove-home', user_name])


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
        running.write('.')
        try:
            run(data)
            q.delete_message(msg)
        except:
            # Handle error
            raise
        finally:
            running.delete()


def test():
    AWS_ACCESS_KEY = '{"Ref": "ManagerKeys"}'
    AWS_SECRET_KEY = '{"Fn::GetAtt": ["ManagerKeys", "SecretAccessKey"]}'
    data = dict(
        project='gitbot/test',
        actions_repo='git://github.com/gitbot/test.git',
        repo='gitbot/www',
        branch='master',
        bucket='releases.dev.gitbot.test',
        keys=dict(access_key=AWS_ACCESS_KEY, secret=AWS_SECRET_KEY),
        command='all'
    )
    run(data)


def main():
    if len(sys.argv) > 1:
        test()
    else:
        poll()

if __name__ == '__main__':
    main()
