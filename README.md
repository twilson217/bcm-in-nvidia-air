# How to setup BCM on AIR environment and make it functional

This document intends to explain how to get BCM operational and basics of onboarding a Cumulus switch and a PXE boot server on AIR environment.
BCM images come in .img.gz format and they are not fully compatible with AIR environment as of time of this README has been written. 
BCM images are provisioned using cloud-init for network configuration and setup of BCM itself, however AIR environment doesn't support cloud-init as of November/2023.
Therefore as a workaound we are deploying BCM cluster ourselves and maintaining the BCM image on AIR platform by ourselves.

## BCM Versioning and builds
| version     | package and build                                  | release date                         |                                                    |
| ----------- | -------------------------------------------------- |------------------------------------- |--------------------------------------------------- |
|10.23.09     | cmdaemon-10.0-156496_cm10.0_ab4640c657.x86_64.rpm  |  Thu 31 Aug 2023 10:18:36 PM CEST    | <-- yours, very old                                |
|10.23.10 +   | cmdaemon-10.0-156589_cm10.0_bb168b4afc.x86_64.rpm  |  Tue 10 Oct 2023 11:27:30 PM CEST    | <-- current public                                 |
|10.23.11 *   | cmdaemon-10.0-156710_cm10.0_403a48ce38.x86_64.rpm  |  Tue 07 Nov 2023 07:28:13 PM CET     | <-- the one we're QA-ing now (and we should use)   |

## Installation and image configuration from scratch

### Download image
The latest GA version of BCM 10.23.10 can be obtained from the following link
https://s3-us-west-1.amazonaws.com/us-west-1.cod-images.support.brightcomputing.com/bcmh-rocky9u2-10.0-4.img.gz

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


3. disable dhcpd service on oob-mgmt-server so that BCM will be the only DHCP server for oob segment and can distribute compute nodes PXE data and ZTP data to Cumulus switches.

```
sudo systemctl disable isc-dhcp-server
sudo service stop isc-dhcp-server
sudo service isc-dhcp-server status
```

4. Start configuring BCM for dhcp and switch / pxe boot
4.1. set dhcp gateway to point towards oob-mgmt-server. From BCM command line type:

```
cmsh
network
use internalnet
set gateway 192.168.200.1
commit
```

4.2. configure leaf01 and leaf02 settings from cmsh console, to achieve this step, we need to know the MAC address of eth0 interface of leaf01 and leaf02

```
cmsh
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

4.3. configure compute0 and compute1 settings from cmsh console, to achieve this step, we need to know the MAC address of eth0 interface of compute0 and compute1

```
cmsh
device
list
device add PhysicalNode compute0 192.168.200.14
set mac 44:38:39:22:AA:04
commit
```





-------------------

localhost login: root
Password:
Last failed login: Fri Oct 27 14:57:26 CEST 2023 on ttyS0
There were 7 failed login attempts since the last successful login.
-----------------------------------------------------------------------

    To initialize the cluster, run:
    cm-bright-setup -c /root/cm/cm-bright-setup.conf

-----------------------------------------------------------------------
REMINDER: the LDAP root password needs to be changed.
REMINDER: the MySQL root password needs to be changed.

To change the LDAP root password:
  slappasswd -h {SSHA}
The encrypted password should be inserted into /etc/openldap/slapd.conf
and OpenLDAP should subsequently be restarted with the following command:
  /etc/init.d/ldap restart

To change the MySQL root password:
  mysqladmin -u root -psystem password <new password>

This warning message can be disabled by executing:
  chmod -x ~/.nag.sh
Can not stat file '/cm/local/apps/cmd/etc/cluster.pem'
Could not open file or uri for loading certificate from /cm/local/apps/cmd/etc/cluster.pem
805B445555150000:error:16000069:STORE routines:ossl_store_get0_loader_int:unregistered scheme:crypto/store/store_register.c:237:scheme=file
805B445555150000:error:80000002:system library:file_open:No such file or directory:providers/implementations/storemgmt/file_store.c:267:calling stat(/cm/local/apps/cmd/etc/cluster.pem)
Unable to load certificate
[root@localhost ~]#

#1### Change all passwords
[root@localhost ~]# grep system cm/cm-bright-setup.conf 
      password: system
cm-change-passwd 

[root@localhost ~]# grep LDAPPass /cm/local/apps/cmd/etc/cmd.conf
LDAPPass = "system"

#2############
dhclient eth1

#2############
ip link set up dev eth0
ip address flush dev eth0
ip addr add 192.168.200.254/255.255.255.0 dev eth0


####default image doesn't contain these, so if we boot from the default image these things need to be set



#2 node-disk-setup.xml###############
/root/cm/node-disk-setup.xml:
<?xml version='1.0' encoding='ISO-8859-1'?>
<diskSetup xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance'>
  <device>
    <blockdev>/dev/vda</blockdev>
    <blockdev>/dev/sda</blockdev>
    <partition id='a2'>
      <size>max</size>
      <type>linux</type>
      <filesystem>xfs</filesystem>
      <mountPoint>/</mountPoint>
      <mountOptions>defaults,noatime,nodiratime</mountOptions>
    </partition>
  </device>
</diskSetup>

#3 cm-bright-setup.conf###############
/root/cm/cm-bright-setup.conf:
modules:
  brightsetup:
     bright:
      admin_email: berkink@nvidia.com
      hostname: bcm-nv-air
      license:
        cluster_name: bcm-nv-air
        country: US
        locality: None
        organization: None
        product_key: 168102-006594-060341-424224-650932
        state: None
        unit: None
      master_compute_node: false
      node_count: 1
      node_disk_setup_path: /root/cm/node-disk-setup.xml
      node_kernel_modules:
      - virtio_net
      - virtio_pci
      - virtio_blk
      password: 3tango
      pbspro_lic_server: ''
      timezone: Europe/Amsterdam
      wlm: ''
      wlm_slot_count: AUTO
    cloud_type: openstack
    openstack:
      head_node_internal_ip: 192.168.200.254
      internal_cidr: 192.168.200.0/24

#4 dns config file##############
/etc/named.conf.global.options.include
dnssec-validation no;
dnssec-enable no;
dnssec-lookaside yes;

service named restart



