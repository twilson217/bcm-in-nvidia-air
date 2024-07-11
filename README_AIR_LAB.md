<!-- AIR:tour -->

# How to setup BCM on AIR environment and make it functional



## BCM Versioning and builds
| version     | package and build                                  | release date                         |                                                    |
| ----------- | -------------------------------------------------- |------------------------------------- |--------------------------------------------------- |
|10.23.09     | cmdaemon-10.0-156496_cm10.0_ab4640c657.x86_64.rpm  |  Thu 31 Aug 2023 10:18:36 PM CEST    |                                  |
|10.23.10 +   | cmdaemon-10.0-156589_cm10.0_bb168b4afc.x86_64.rpm  |  Tue 10 Oct 2023 11:27:30 PM CEST    |                                  |
|10.23.11     | cmdaemon-10.0-156713_cm10.0_14e56b67c0.x86_64.rpm  |  Tue 14 Nov 2023 09:05:48 PM CET     |                                  |
|10.23.12 *+  | cmdaemon-10.0-156921_cm10.0_5d3db827b4.x86_64.rpm  |  Thu 07 Dec 2023 07:04:39 PM CET     |                                  |
|10.24.03     | bcmh-rocky9u3-10.0-2.img.gz                        |                                      |                                  |  


| Node name     | interface | IP address        |  MAC address        |
| ------------- | --------- |------------------ |-------------------- |
| leaf01        | eth0      |  192.168.200.12   | 44:38:39:22:AA:02   |
| leaf02        | eth0      |  192.168.200.13   | 44:38:39:22:AA:03   |
| compute0      | eth0      |  192.168.200.14   | 44:38:39:22:AA:04   |
| compute1      | eth0      |  192.168.200.15   | 44:38:39:22:AA:05   |
| BCM           | eth0      |  192.168.200.254  | random              |

device add PhysicalNode compute1 192.168.200.15
set mac 44:38:39:22:AA:05
commit

device add PhysicalNode compute2 192.168.200.16
set mac 44:38:39:22:AA:06
commit


<!-- AIR:page -->

## Installation and image configuration from scratch

### Download image
Previous versions of BCM10  
https://s3-us-west-1.amazonaws.com/us-west-1.cod-images.support.brightcomputing.com/bcmh-rocky9u2-10.0-4.img.gz  
https://s3-us-west-1.amazonaws.com/us-west-1.cod-images.support.brightcomputing.com/bcmh-rocky9u2-10.0-8.img.gz  

The latest GA version of BCM 10.24.03 can be obtained from the following link  
https://s3.us-west-1.amazonaws.com/us-west-1.cod-images.support.brightcomputing.com/bcmh-rocky9u3-10.0-2.img.gz

`https://s3-us-west-1.amazonaws.com/us-west-1.cod-images.support.brightcomputing.com/imagerepo.yaml` file includes all the versions and builds for corresponding image file
  


As per AIR documentation on how to upload and maintain image files, details are explained in [Image Upload Process](https://confluence.nvidia.com/display/NetworkingBU/Image+Upload+Process):
We are only allowed to upload a qcow2 or iso format, we must convert this .img.gz into a qcow2 image format.

Since I'm using Windows with WSL, I first downloaded the image and using 7-zip I unpacked .img file.
Then, copy the file on WSL linux partition and converted the image to qcow2 format.

### Convert to qcow2
`sudo qemu-img convert -f raw -O qcow2 bcmh-rocky9u2-10.0-8.img bcmh-rocky9u2-10.0-8.qcow2`

### Set root password
As I already know (by experience) this image file doesn't have a root password set and any of the network interfaces configured, I plan to use external tools to mount the image file and do this very basic configuration offline, without booting the image file. Follow the instructions from AIR documentation on [Working with qcow2 images](https://confluence.nvidia.com/display/NetworkingBU/Working+with+qcow2+images)
```
sudo apt install -y linux-image-generic
sudo apt install -y guestfs-tools
sudo virt-sysprep -a bcmh-rocky9u2-10.0-8.qcow2 --password root:password:centos
```

<!-- AIR:page -->

### Configure Network Interfaces
Mount image file from a tool called gustfish
`sudo guestfish --rw -a bcmh-rocky9u2-10.0-8.qcow2`  

```
 
```  

edit `ifcfg-eth0` file using the `vi` editor and configure it with a static IP. This will be our internal interface, looking towards oob management network.

```

```


```
><fs> touch /etc/sysconfig/network-scripts/ifcfg-eth1
```

### Add configuration files used by BCM


`><fs> touch /root/cm/`[node-disk-setup.xml](node-disk-setup.xml)  
`><fs> touch /root/cm/`[cm-bright-setup.conf](cm-bright-setup.conf)  
`><fs> touch /etc/`[named.conf.global.options.include](named.conf.global.options.include)  


### Finish


### Upload the image on AIR and share it with yourself to be able to use

Based on [Image Upload Process](https://confluence.nvidia.com/display/NetworkingBU/Image+Upload+Process), upload the image on AIR and make sure image is shared with yourself.

<!-- AIR:page -->

## Starting the Simulation for two leaf switches, two PXE boot servers and BCM



[test-bcm.dot](test-bcm.dot)  
[cumulus-ztp.sh](cumulus-ztp.sh)  


### Configuring BCM virtual machine after the first boot

1. 


2. 
3. 

4. 
5. 

<!-- AIR:page -->

6.

<!-- AIR:page -->
