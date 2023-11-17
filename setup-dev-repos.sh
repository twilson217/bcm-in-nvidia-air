#!/bin/bash -e
#
# Run this script once on the head node.
#
# Then using the commands below update as needed from the nightly builds
#
# apt update
# apt install cmdaemon base-view
# cm-chroot-sw-img /cm/images/default-image 'apt update; apt install cmdaemon'
# cm-chroot-sw-img /cm/node-installer 'apt update; apt install cmdaemon-node-installer'
#
# yum clean all
# yum update cmdaemon base-view
# yum --installroot /cm/images/default-image clean all
# yum --installroot /cm/images/default-image update cmdaemon
# yum --installroot /cm/node-installer clean all
# yum --installroot /cm/node-installer update cmdaemon-node-installer
#

ubuntu() {
  path=$1
  os=$(echo "$2" | tr -d '.')
  if [ $os == "1804" ]; then
    echo "machine updates-testing.brightcomputing.com/nightlybuilds/apt login nightly password 82Adg82cxX" > $path/etc/apt/auth.conf.d/cm-nightly.conf
  else
    echo "machine http://updates-testing.brightcomputing.com/nightlybuilds/apt login nightly password 82Adg82cxX" > $path/etc/apt/auth.conf.d/cm-nightly.conf
  fi
  echo "deb [trusted=yes] http://updates-testing.brightcomputing.com/nightlybuilds/apt/cm/\$(ARCH)/$bright/ubuntu/$os/base/ ./" > $path/etc/apt/sources.list.d/cm.list
  echo "deb [trusted=yes] http://updates-testing.brightcomputing.com/nightlybuilds/apt/ml/\$(ARCH)/$bright/ubuntu/$os/base/ ./" > $path/etc/apt/sources.list.d/cm-ml.list
}

rhel() {
  path=$1
  os=${2%%.*}
  for f in cm cm-ml cm-ni; do
    if [ -e "$path/etc/yum.repos.d/$f.repo" ]
    then
      perl -pi -e 's#enabled=1#enabled=0#g' $path/etc/yum.repos.d/$f.repo
    fi
  done

cat <<EOF > $path/etc/yum.repos.d/cm-nightly.repo
[cm-nightly]
name=Cluster Manager Nightly Updates
baseurl=http://updates-testing.brightcomputing.com/nightlybuilds/yum/cm/x86_64/$bright/rhel/$os/base/
username=nightly
password=82Adg82cxX
enabled=1
priority=11
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-cm
exclude=parted
EOF
}

switch() {
  path=$1
  source $path/etc/os-release
  ID=${ID^^}
  if [ "$ID" = "UBUNTU" ]; then
    ubuntu "$path" "$VERSION_ID"
  elif [ "$ID" = "CENTOS" -o "$ID" = "RHEL" -o "$ID" = "ROCKY" ]; then
    rhel "$path" "$VERSION_ID"
  elif [ "$ID" = "SLES" -o "$ID" = "SUSE" ]; then
    echo "$path is $ID (TODO)"
    exit 1
  else
    echo "$path is $ID (unknown os)"
    exit 1
  fi
}

bright=$(cat /etc/cm-release | cut -dv -f2 | head -n1)
echo "=== / ($bright) ==="
switch "/"

ap=$(cat /var/spool/cmd/state)
if [ "$ap" == "ACTIVE" ]; then
  for image in $(ls -d /cm/node-installer*); do
    echo "=== $image ==="
    switch "$image"
  done
  for image in $(find /cm/images/ -mindepth 1 -maxdepth 1 -type d); do
    if [[ $(basename $image) =~ \-[0-9]+$ ]]; then
      echo "Skip image revision: $image"
    else
      echo "=== $image ==="
      switch "$image"
    fi
  done
fi

for os in "1804" "2004" "2204"; do
  echo "=== $os ==="
  echo "deb [trusted=yes] http://updates-testing.brightcomputing.com/nightlybuilds/apt/cm/\$(ARCH)/$bright/ubuntu/$os/base/ ./" > /cm/local/apps/cluster-tools/repoconfig/cm.repo.ubuntu$os
  if [ $os == "1804" ]; then
    echo "machine updates-testing.brightcomputing.com/nightlybuilds/apt login nightly password 82Adg82cxX" > /cm/local/apps/cluster-tools/repoconfig/cm.auth.conf.ubuntu$os
  else
    echo "machine http://updates-testing.brightcomputing.com/nightlybuilds/apt login nightly password 82Adg82cxX" > /cm/local/apps/cluster-tools/repoconfig/cm.auth.conf.ubuntu$os
  fi
done