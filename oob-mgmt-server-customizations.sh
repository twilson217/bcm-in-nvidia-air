#!/bin/bash
#
# This runs as root. Users use the system as cumulus
# mind your chmods and chowns for things you want user cumulus to use
#
passwd -d ubuntu
echo 'ubuntu:nvidia' | chpasswd
chage -m 1 -M 1 ubuntu
su ubuntu -c 'pip3 install git+https://gitlab.com/cumulus-consulting/air/cumulus_air_sdk.git'
su ubuntu -c 'pip3 install helpers'
#touch /home/ubuntu/.ssh/authorized_keys
rm -rf /home/ubuntu/Cumulus-Linux-demo
# clone cumulus_ansible_modules repo to Cumulus-Linux-demo folder
cd /home/ubuntu/
git clone https://gitlab.com/cumulus-consulting/goldenturtle/cumulus_ansible_modules.git /home/ubuntu/Cumulus-Linux-demo
cd /home/ubuntu/Cumulus-Linux-demo; git checkout evpn_demo_nvue_5.x
sudo chown -R ubuntu:ubuntu /home/ubuntu/


# add some ansible ssh convenience settings so ad hoc ansible works easily
cat <<EOT > /etc/ansible/ansible.cfg

[defaults]
roles_path = ./roles
host_key_checking = False
pipelining = True
forks = 50
deprecation_warnings = False
jinja2_extensions = jinja2.ext.do
force_handlers = True
retry_files_enabled = False
transport = paramiko
ansible_managed = # Ansible Managed File
# Time the task execution
callback_whitelist = profile_tasks
# Use the YAML callback plugin.
stdout_callback = yaml
# Use the stdout_callback when running ad-hoc commands.
# bin_ansible_callbacks = True
interpreter_python = auto_silent
#strategy = free
allow_world_readable_tmpfiles = True

[ssh_connection]
ssh_args = -o ControlMaster=auto -o ControlPersist=60s -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null

EOT
chmod -R 755 /etc/ansible/*

# echo 'export ANSIBLE_LOG_PATH=/home/ubuntu/ansible_log.log' >> /home/ubuntu/.profile

# disable ssh key checking
cat <<EOT > /home/ubuntu/.ssh/config
Host *
    StrictHostKeyChecking no
EOT
chown -R ubuntu:ubuntu /home/ubuntu/.ssh
sed -i 's/PasswordAuthentication no/PasswordAuthentication yes/g' /etc/ssh/sshd_config
rm /home/ubuntu/.ssh/authorized_keys

apt update -qy
apt install -qy ntp

# Need gitlab-runner now that we're on ubuntu
# apt install gitlab-runner -y

# Install classical net-tools package
apt install net-tools -y

# Install traceroute package
apt install traceroute -y

# Install python3-netaddr package for ansible
apt install python3-netaddr -y 

# Install snmp tools package
apt install snmp -y 

# Update ansible hosts to connect to servers
echo '

[host:vars]
ansible_user=ubuntu
ansible_become_pass=nvidia
ansible_ssh_pass=nvidia' >> /etc/ansible/hosts
