[root@bcm-nv-air ~]# cmsh
[bcm-nv-air]% list
beegfs .......................... [  OK  ]
category ........................ [  OK  ]
ceph ............................ [  OK  ]
cert ............................ [  OK  ]
cloud ........................... [  OK  ]
cmjob ........................... [  OK  ]
configurationoverlay ............ [  OK  ]
device .......................... [  OK  ]
edgesite ........................ [  OK  ]
etcd ............................ [  OK  ]
fspart .......................... [  OK  ]
group ........................... [  OK  ]
hierarchy ....................... [  OK  ]
kubernetes ...................... [  OK  ]
main ............................ [  OK  ]
monitoring ...................... [  OK  ]
network ......................... [  OK  ]
nodegroup ....................... [  OK  ]
partition ....................... [  OK  ]
process ......................... [  OK  ]
profile ......................... [  OK  ]
rack ............................ [  OK  ]
session ......................... [  OK  ]
softwareimage ................... [  OK  ]
task ............................ [  OK  ]
unmanagednodeconfiguration ...... [  OK  ]
user ............................ [  OK  ]
wlm ............................. [  OK  ]

[bcm-nv-air]% device
[bcm-nv-air->device]% list
Type                   Hostname (key)   MAC                Category         Ip              Network        Status
---------------------- ---------------- ------------------ ---------------- --------------- -------------- -----------------------
HeadNode               bcm-nv-air       48:B0:2D:C3:B1:42                   192.168.200.254 internalnet    [   UP   ]
PhysicalNode           node001          00:00:00:00:00:00  default          192.168.200.1   internalnet    [  DOWN  ], unassigned

use bcm-nv-air
==========================

device add switch leaf01 192.168.200.12
set mac 48:b0:2d:b9:52:84
set disablesnmp yes
set hasclientdaemon yes
ztpsettings 
set enableapi yes
commit

device add PhysicalNode compute0 192.168.200.14
set mac 48:b0:2d:ce:61:02
commit

device add PhysicalNode compute1 192.168.200.15
set mac 48:b0:2d:ce:61:02
commit

cumulus:
sudo ztp -e
sudo reboot


### changing system dns ###
partition
use base
set nameservers 192.168.200.1
commmit

### dnssec related error from BCM ###
rndc managed-keys destroy; rndc reconfig
## other dns related errors
cat /etc/named.conf.global.options.include 
dnssec-validation no;
dnssec-enable no;
dnssec-lookaside yes;

service named restart

### weird dns issue
run the script
and after : (ubuntu)
apt update
apt install cmdaemon base-view
cm-chroot-sw-img /cm/images/default-image 'apt update; apt install cmdaemon'
cm-chroot-sw-img /cm/node-installer 'apt update; apt install cmdaemon-node-installer'
(rhel)
yum clean all
yum update cmdaemon base-view
cm-chroot-sw-img /cm/images/default-image 'yum clean all; yum update cmdaemon'
cm-chroot-sw-img /cm/node-installer 'yum clean all; yum install cmdaemon-node-installer'

[root@bcm-nv-air ~]# cmd -v
Fri Nov 10 14:51:00 2023 [   CMD   ]   Info: CMDaemon version 3.0 (156496_ab4640c657)
Fri Nov 10 14:51:00 2023 [   CMD   ]   Info: CM version 10.0
Fri Nov 10 14:51:00 2023 [   CMD   ]   Info: CM API hash 1e4d7b993d8f2a4d8fb375eced4e0f8ccc31b8818bdb8f8d319642778aafc42fabc47726c74929effa60ccaccff5f7fec4d07fb5668efd2a000c3d7e5d7c51eb
Fri Nov 10 14:51:00 2023 [   CMD   ]   Info: This binary was compiled on Aug 31 2023, 22:30:08


#### Switch ####
ztp -e  # enable ZTP
ztp -s # status