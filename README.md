<!-- AIR:tour -->

# How to setup BCM on AIR environment and make it functional

This document intends to explain how to get BCM operational and basics of onboarding a Cumulus switch and a PXE boot server on AIR environment.
BCM images come in .img.gz format and they are not fully compatible with AIR environment as of time of this README has been written. 
BCM images are provisioned using cloud-init for network configuration and setup of BCM itself, however AIR environment doesn't support cloud-init as of November/2023.
Therefore as a workaound we are deploying BCM cluster ourselves and maintaining the BCM image on AIR platform by ourselves.

## BCM Versioning and builds
| version     | package and build                                  | release date                         |                                                    |
| ----------- | -------------------------------------------------- |------------------------------------- |--------------------------------------------------- |
|10.23.09     | cmdaemon-10.0-156496_cm10.0_ab4640c657.x86_64.rpm  |  Thu 31 Aug 2023 10:18:36 PM CEST    |                                  |
|10.23.10 +   | cmdaemon-10.0-156589_cm10.0_bb168b4afc.x86_64.rpm  |  Tue 10 Oct 2023 11:27:30 PM CEST    |                                  |
|10.23.11     | cmdaemon-10.0-156713_cm10.0_14e56b67c0.x86_64.rpm  |  Tue 14 Nov 2023 09:05:48 PM CET     |                                  |
|10.23.12 *+  | cmdaemon-10.0-156921_cm10.0_5d3db827b4.x86_64.rpm  |  Thu 07 Dec 2023 07:04:39 PM CET     |                                  |

## Installation and image configuration from scratch

### Download image
The latest GA version of BCM 10.23.10 can be obtained from the following link  
https://s3-us-west-1.amazonaws.com/us-west-1.cod-images.support.brightcomputing.com/bcmh-rocky9u2-10.0-4.img.gz  
https://s3-us-west-1.amazonaws.com/us-west-1.cod-images.support.brightcomputing.com/bcmh-rocky9u2-10.0-8.img.gz  
  
  

As per AIR documentation on how to upload and maintain image files, details are explained in [Image Upload Process](https://confluence.nvidia.com/display/NetworkingBU/Image+Upload+Process):
We are only allowed to upload a qcow2 or iso format, we must convert this .img.gz into a qcow2 image format.

Since I'm using Windows with WSL, I first downloaded the image and using 7-zip I unpacked .img file.
Then, copy the file on WSL linux partition and converted the image to qcow2 format.

### Convert to qcow2
`sudo qemu-img convert -f raw -O qcow2 bcmh-rocky9u2-10.0-4.img bcmh-rocky9u2-10.0-4.qcow2`

### Set root password
As I already know (by experience) this image file doesn't have a root password set and any of the network interfaces configured, I plan to use external tools to mount the image file and do this very basic configuration offline, without booting the image file. Follow the instructions from AIR documentation on [Working with qcow2 images](https://confluence.nvidia.com/display/NetworkingBU/Working+with+qcow2+images)
```
sudo apt install -y linux-image-generic
sudo apt install -y guestfs-tools
sudo virt-sysprep -a bcmh-rocky9u2-10.0-4.qcow2 --password root:password:centos
```

### Configure Network Interfaces
Mount image file from a tool called gustfish
`sudo guestfish --rw -a CentOS-8-GenericCloud-8.4.2105-20210603.0.x86_64.qcow2`  

```
><fs> run  
><fs> list-filesystems  
><fs> mount /dev/vda1 /  
><fs> touch /etc/sysconfig/ifcfg-eth0  
```  

edit `ifcfg-eth0` file using the `vi` editor and configure it with a static IP. This will be our internal interface, looking towards oob management network.

```
TYPE="Ethernet"  
BOOTPROTO="static"  
NAME="eth0"  
DEVICE="eth0"  
ONBOOT="yes"  
IPADDR=192.168.200.254  
NETMASK=255.255.255.0  
```
repeat the same process for `ifcfg-eth1`, but configure it to obtain an IP address using DHCP. This will be our external interface, looking towards internet where we can connect from outside world.

```
><fs> touch /etc/sysconfig/ifcfg-eth1
```
```
TYPE="Ethernet"  
BOOTPROTO="dhcp"  
NAME="eth1"  
DEVICE="eth1"  
ONBOOT="yes"  
```
### Add configuration files used by BCM


`><fs> touch /root/cm/`[node-disk-setup.xml](node-disk-setup.xml)  
`><fs> touch /root/cm/`[cm-bright-setup.conf](cm-bright-setup.conf)  
`><fs> touch /etc/`[named.conf.global.options.include](named.conf.global.options.include)  


### Finish
Finally unmount the image file and exit. Your image file is ready to be used in BCM environment.
```
><fs> umount /  
><fs> exit  
```

### Upload the image on AIR and share it with yourself to be able to use

Based on [Image Upload Process](https://confluence.nvidia.com/display/NetworkingBU/Image+Upload+Process), upload the image on AIR and make sure image is shared with yourself.

## Starting the Simulation for two leaf switches, two PXE boot servers and BCM

Using the following .dot file and ztp script, start a custom topology and connect using `root/3tango` account credentials to BCM virtual machine. 
As usual `oob-mgmt-server` has `ubuntu/nvidia` and cumulus switches `cumulus/CumulusLinux!` username password combinations. Once PXE boot and provisioning is sucessful both compute nodes will have the same login credentials (`root/3tango`) as BCM head node.

[test-bcm.dot](test-bcm.dot)  
[cumulus-ztp.sh](cumulus-ztp.sh)  


### Configuring BCM virtual machine after the first boot

1. grow vda1 partition to occupy whole virtual disk (200GB)
```
growpart /dev/vda 1  
xfs_growfs /
```

2. install BMC10 using the following command
`cm-bright-setup -c /root/cm/cm-bright-setup.conf --on-error-action abort`

If you'd like to change the default root password after the installation, run the following command:
```
cm-change-passwd 
```

You can check the BCM version installed using the following command on BCM shell:

```
[root@localhost ~]# cmd -v
Mon Nov 20 11:06:29 2023 [   CMD   ]   Info: CMDaemon version 3.0 (156589_bb168b4afc)  <==== Current build in the qcow2 image uploaded on AIR
Mon Nov 20 11:06:29 2023 [   CMD   ]   Info: CM version 10.0
Mon Nov 20 11:06:29 2023 [   CMD   ]   Info: CM API hash 1e4d7b993d8f2a4d8fb375eced4e0f8ccc31b8818bdb8f8d319642778aafc42fabc47726c74929effa60ccaccff5f7fec4d07fb5668efd2a000c3d7e5d7c51eb
Mon Nov 20 11:06:29 2023 [   CMD   ]   Info: This binary was compiled on Oct 10 2023, 23:22:18

```

As of time of this writing, unfortunately we faced several bugs that prevented Cumulus switches to be onboarded into BCM. For sucessful onboarding we had to disable GA repositories and switch to nightly builds in order to proceed.  
Therefore the following script must be run on BCM virtual machine to enable nightly builds.  
[setup-dev-repos.sh](setup-dev-repos.sh)  
It's alread copied under `/root` folder, so run the script using the following prompt:
```
[root@localhost ~]# bash setup-dev-repos.sh
=== / (10.0) ===
=== /cm/node-installer ===
=== /cm/images/default-image ===
=== 1804 ===
=== 2004 ===
=== 2204 ===
```

After running this script the following commands must be run:  
```
yum clean all
yum update cmdaemon base-view -y
yum --installroot /cm/images/default-image clean all -y
yum --installroot /cm/images/default-image update cmdaemon -y
yum --installroot /cm/node-installer clean all -y
yum --installroot /cm/node-installer update cmdaemon-node-installer -y
```

After nightly build upgrades, as of this documents writing, we can observe the build number as follows:

```
[root@localhost ~]# cmd -v
Mon Nov 20 11:53:58 2023 [   CMD   ]   Info: CMDaemon version 3.0 (156809_649e91203c)
Mon Nov 20 11:53:58 2023 [   CMD   ]   Info: CM version 10.0
Mon Nov 20 11:53:58 2023 [   CMD   ]   Info: CM API hash 1e4d7b993d8f2a4d8fb375eced4e0f8ccc31b8818bdb8f8d319642778aafc42fabc47726c74929effa60ccaccff5f7fec4d07fb5668efd2a000c3d7e5d7c51eb
Mon Nov 20 11:53:58 2023 [   CMD   ]   Info: This binary was compiled on Nov 17 2023, 22:29:41

```

3. disable dhcpd service on oob-mgmt-server so that BCM will be the only DHCP server for oob segment and can distribute compute nodes PXE info and ZTP script to Cumulus switches.

```
sudo systemctl disable isc-dhcp-server
sudo service isc-dhcp-server stop
sudo service isc-dhcp-server status
```

4. Start configuring BCM for dhcp and switch / pxe boot  
In order to configure things on BCM first you need to be on BCM console, type the following command on BCM shell:  
```
[root@localhost ~]# cmsh
[bcm-nv-air]%
```

4.1. set dhcp gateway to point towards oob-mgmt-server. From BCM command line type:

```
network
use internalnet
set gateway 192.168.200.1
commit
```

4.2. Configure leaf01 and leaf02 settings from cmsh console, to achieve this step, we reserved the following IP / MAC addresses for each node:

| Node name     | interface | IP address        |  MAC address        |
| ------------- | --------- |------------------ |-------------------- |
| leaf01        | eth0      |  192.168.200.12   | 44:38:39:22:AA:02   |
| leaf02        | eth0      |  192.168.200.13   | 44:38:39:22:AA:03   |
| compute0      | eth0      |  192.168.200.14   | 44:38:39:22:AA:04   |
| compute1      | eth0      |  192.168.200.15   | 44:38:39:22:AA:05   |
| BCM           | eth0      |  192.168.200.254  | random              |


Configure the following parameters on BCM for leaf01:
```
device
list
device add switch leaf01 192.168.200.12
set mac 44:38:39:22:AA:02
set disablesnmp yes
set hasclientdaemon yes
ztpsettings 
set enableapi yes
commit
```

Logon to leaf01 and enable ztp process and reboot the switch:

```
sudo ztp -e
sudo reboot
```

4.3. Repeat the same process for leaf02 on BCM:

```
device
list
device add switch leaf02 192.168.200.13
set mac 44:38:39:22:AA:03
set disablesnmp yes
set hasclientdaemon yes
ztpsettings 
set enableapi yes
commit
```

on leaf02 console:
```
sudo ztp -e
sudo reboot
```


4.4. Configure compute0 and compute1 settings from cmsh console, to achieve this step, we already know the MAC address of eth0 interfaces of both nodes from the table above

```
device
list
device add PhysicalNode compute0 192.168.200.14
set mac 44:38:39:22:AA:04
commit
```
then add compute1
```
device add PhysicalNode compute1 192.168.200.15
set mac 44:38:39:22:AA:05
commit
```
4.5. Reboot compute nodes so PXE boot process starts again.

4.6. From BCM `cmsh` command line prompt, check the status of devices and wait till they become `UP`

```
device
list
[bcm-nv-air->device]% list
Type                   Hostname (key)   MAC                Category         Ip              Network        Status
---------------------- ---------------- ------------------ ---------------- --------------- -------------- --------------------------------
HeadNode               bcm-nv-air       48:B0:2D:11:FE:92                   192.168.200.254 internalnet    [   UP   ]
PhysicalNode           node001          00:00:00:00:00:00  default          192.168.200.1   internalnet    [  DOWN  ], unassigned
Switch                 leaf01           44:38:39:22:AA:02                   192.168.200.12  internalnet    [   UP   ]
Switch                 leaf02           44:38:39:22:AA:03                   192.168.200.13  internalnet    [       BOOTING       ] (/switc+
```

```
Type                   Hostname (key)   MAC                Category         Ip              Network        Status
---------------------- ---------------- ------------------ ---------------- --------------- -------------- --------------------------------
HeadNode               bcm-nv-air       48:B0:2D:11:FE:92                   192.168.200.254 internalnet    [   UP   ]
PhysicalNode           compute0         44:38:39:22:AA:04  default          192.168.200.14  internalnet    [     INSTALLING      ] (provis+
PhysicalNode           compute1         44:38:39:22:AA:05  default          192.168.200.15  internalnet    [  DOWN  ]
PhysicalNode           node001          00:00:00:00:00:00  default          192.168.200.1   internalnet    [  DOWN  ], unassigned
Switch                 leaf01           44:38:39:22:AA:02                   192.168.200.12  internalnet    [   UP   ]
Switch                 leaf02           44:38:39:22:AA:03                   192.168.200.13  internalnet    [   UP   ]
```

```
Type                   Hostname (key)   MAC                Category         Ip              Network        Status
---------------------- ---------------- ------------------ ---------------- --------------- -------------- --------------------------------
HeadNode               bcm-nv-air       48:B0:2D:11:FE:92                   192.168.200.254 internalnet    [   UP   ]
PhysicalNode           compute0         44:38:39:22:AA:04  default          192.168.200.14  internalnet    [ INSTALLER_CALLINGINIT ] (swit+
PhysicalNode           compute1         44:38:39:22:AA:05  default          192.168.200.15  internalnet    [  DOWN  ]
PhysicalNode           node001          00:00:00:00:00:00  default          192.168.200.1   internalnet    [  DOWN  ], unassigned
Switch                 leaf01           44:38:39:22:AA:02                   192.168.200.12  internalnet    [   UP   ]
Switch                 leaf02           44:38:39:22:AA:03                   192.168.200.13  internalnet    [   UP   ]
```

```
Type                   Hostname (key)   MAC                Category         Ip              Network        Status
---------------------- ---------------- ------------------ ---------------- --------------- -------------- -----------------------
HeadNode               bcm-nv-air       48:B0:2D:11:FE:92                   192.168.200.254 internalnet    [   UP   ]
PhysicalNode           compute0         44:38:39:22:AA:04  default          192.168.200.14  internalnet    [   UP   ]
PhysicalNode           compute1         44:38:39:22:AA:05  default          192.168.200.15  internalnet    [  DOWN  ]
PhysicalNode           node001          00:00:00:00:00:00  default          192.168.200.1   internalnet    [  DOWN  ], unassigned
Switch                 leaf01           44:38:39:22:AA:02                   192.168.200.12  internalnet    [   UP   ]
Switch                 leaf02           44:38:39:22:AA:03                   192.168.200.13  internalnet    [   UP   ]
```

For switches at first you will see "BOOTING", then after ZTP process completes and registers cm-lite-daemon service, you will see "UP". This might take a couple of minutes depending on your network speed.
For compute nodes in the first stages of PXE boot you will see "BOOTING", then "INSTALLING", "INSTALLER_CALLINGINIT" and finally "UP"


4.7. The status of devices can be observed from BCM GUI as well, to do this we need to use `ADD SERVICE` function of AIR and map TCP 8081 port of BCM head node to an externally reachable url/port combination.
After this step, BCM GUI can be accessible from the following URLs:
```
https://<worker_url>:<tcp_port>/userportal
https://<worker_url>:<tcp_port>/base-view
```

<!-- AIR:page -->

If you'd like to enable air_agent on BCM machine so that you can control the virtual machine using AIR SDK, please clone the air_sdk repository and install it.

```
[root@localhost ~]# git clone https://github.com/NVIDIA/air_agent.git
Cloning into 'air_agent'...
remote: Enumerating objects: 590, done.
remote: Counting objects: 100% (360/360), done.
remote: Compressing objects: 100% (163/163), done.
remote: Total 590 (delta 255), reused 288 (delta 195), pack-reused 230
Receiving objects: 100% (590/590), 186.76 KiB | 2.92 MiB/s, done.
Resolving deltas: 100% (333/333), done.
[root@localhost ~]# cd air_agent/

[root@localhost air_agent]# ./install.sh
####################################
# Installing pip requirements      #
####################################
Processing /root/air_agent
  DEPRECATION: A future pip version will change local packages to be built in-place without first copying to a temporary directory. We recommend you use --use-feature=in-tree-build to test your packages with this new behavior before it becomes the default.
   pip 21.3 will remove support for this functionality. You can find discussion regarding this at https://github.com/pypa/pip/issues/7555.
  Installing build dependencies ... done
  Getting requirements to build wheel ... done
    Preparing wheel metadata ... done
Collecting cryptography==41.0.3
  Downloading cryptography-41.0.3-cp37-abi3-manylinux_2_28_x86_64.whl (4.3 MB)
     |████████████████████████████████| 4.3 MB 6.7 MB/s
Collecting requests==2.31.0
  Downloading requests-2.31.0-py3-none-any.whl (62 kB)
     |████████████████████████████████| 62 kB 5.0 MB/s
Collecting gitpython==3.1.35
  Downloading GitPython-3.1.35-py3-none-any.whl (188 kB)
     |████████████████████████████████| 188 kB 76.7 MB/s
Requirement already satisfied: cffi>=1.12 in /usr/lib64/python3.9/site-packages (from cryptography==41.0.3->agent==3.0.2) (1.14.5)
Collecting gitdb<5,>=4.0.1
  Downloading gitdb-4.0.11-py3-none-any.whl (62 kB)
     |████████████████████████████████| 62 kB 5.8 MB/s
Requirement already satisfied: idna<4,>=2.5 in /usr/lib/python3.9/site-packages (from requests==2.31.0->agent==3.0.2) (2.10)
Requirement already satisfied: urllib3<3,>=1.21.1 in /usr/lib/python3.9/site-packages (from requests==2.31.0->agent==3.0.2) (1.26.5)
Collecting certifi>=2017.4.17
  Downloading certifi-2023.11.17-py3-none-any.whl (162 kB)
     |████████████████████████████████| 162 kB 107.3 MB/s
Collecting charset-normalizer<4,>=2
  Downloading charset_normalizer-3.3.2-cp39-cp39-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (142 kB)
     |████████████████████████████████| 142 kB 70.6 MB/s
Requirement already satisfied: pycparser in /usr/lib/python3.9/site-packages (from cffi>=1.12->cryptography==41.0.3->agent==3.0.2) (2.20)
Collecting smmap<6,>=3.0.1
  Downloading smmap-5.0.1-py3-none-any.whl (24 kB)
Requirement already satisfied: ply==3.11 in /usr/lib/python3.9/site-packages (from pycparser->cffi>=1.12->cryptography==41.0.3->agent==3.0.2) (3.11)
Building wheels for collected packages: agent
  Building wheel for agent (PEP 517) ... done
  Created wheel for agent: filename=agent-3.0.2-py3-none-any.whl size=12675 sha256=0f69636ca17cdb4c9f0238023cd9c7483c532058d01ab010786c08ed08cae8bf
  Stored in directory: /tmp/pip-ephem-wheel-cache-wk_0ry9m/wheels/50/d2/4c/f2c8aad5fb8ab176c6ef4a732c23184494bb4dc8f43a02d0f2
Successfully built agent
Installing collected packages: smmap, gitdb, charset-normalizer, certifi, requests, gitpython, cryptography, agent
Successfully installed agent-3.0.2 certifi-2023.11.17 charset-normalizer-3.3.2 cryptography-41.0.3 gitdb-4.0.11 gitpython-3.1.35 requests-2.31.0 smmap-5.0.1
WARNING: Running pip as the 'root' user can result in broken permissions and conflicting behaviour with the system package manager. It is recommended to use a virtual environment instead: https://pip.pypa.io/warnings/venv
Done!
####################################
# Installing air-agent             #
####################################
Done!
####################################
# Enabling systemd service         #
####################################
Created symlink /etc/systemd/system/multi-user.target.wants/air-agent.service → /etc/systemd/system/air-agent.service.
Done!
[root@localhost air_agent]# ps -ef | grep air
root       71805       1  1 12:12 ?        00:00:00 /usr/bin/python3 /usr/local/lib/air-agent/agent.py
root       86919    1841  0 12:12 pts/0    00:00:00 grep --color=auto air
[root@localhost air_agent]# more /var/log/air-agent.log
2023-11-20 12:12:10,901 INFO Syncing clock from hypervisor
2023-11-20 12:12:12,010 INFO Restarting chronyd.service
2023-11-20 12:12:12,053 INFO Checking for updates
2023-11-20 12:12:12,247 INFO Initializing with identity b863bbde-8ebe-4bcc-91ae-3906a0cf3c7d
2023-11-20 12:12:12,250 WARNING Platform detection failed to determine OS
2023-11-20 12:12:12,250 INFO Starting Air Agent daemon v3.0.2
[root@localhost air_agent]# more /var/log/air-agent.log
2023-11-20 12:12:10,901 INFO Syncing clock from hypervisor
2023-11-20 12:12:12,010 INFO Restarting chronyd.service
2023-11-20 12:12:12,053 INFO Checking for updates
2023-11-20 12:12:12,247 INFO Initializing with identity b863bbde-8ebe-4bcc-91ae-3906a0cf3c7d
2023-11-20 12:12:12,250 WARNING Platform detection failed to determine OS
2023-11-20 12:12:12,250 INFO Starting Air Agent daemon v3.0.2
[root@localhost air_agent]#

```

<!-- AIR:page -->
