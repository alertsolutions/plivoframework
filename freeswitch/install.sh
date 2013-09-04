#!/bin/bash

# FreeSWITCH Installation script for CentOS 5.5/5.6
# and Debian based distros (Debian 5.0 , Ubuntu 10.04 and above)
# Copyright (c) 2011 Plivo Team. See LICENSE for details.


FS_CONF_PATH=https://github.com/asajohnston/plivoframework/raw/master/freeswitch
FS_GIT_REPO=git://git.freeswitch.org/freeswitch.git
FS_INSTALLED_PATH=/usr/local/freeswitch

#####################################################
FS_BASE_PATH=/usr/local/src/
#####################################################

CURRENT_PATH=$PWD

git_arg=''
fax_install=false
while [ "$1" != "" ] ; do
    case $1 in
        "-s"|"--stable")
            git_arg='-b v1.2.stable'
        ;;
        "-f"|"--fax")
            fax_install=true
        ;;
        "-h"|"--help")
            echo "$(basename $0) [ --stable ] [ --fax | --voice ]"
            echo "install freeswitch (options: use stable branch, install for fax or voice) (default: mainline voice)"
            exit 0
        ;;
    esac
    shift
done

# Identify Linux Distribution
if [ -f /etc/debian_version ] ; then
    DIST="DEBIAN"
elif [ -f /etc/redhat-release ] ; then
    DIST="CENTOS"
else
    echo ""
    echo "This Installer should be run on a CentOS or a Debian based system"
    echo ""
    exit 1
fi


clear
echo ""
echo "FreeSWITCH will be installed in $FS_INSTALLED_PATH"
echo "Press any key to continue or CTRL-C to exit"
echo ""
#read INPUT


echo "Setting up Prerequisites and Dependencies for FreeSWITCH"
case $DIST in
    'DEBIAN')
        apt-get -y update
        apt-get -y install autoconf automake autotools-dev binutils bison build-essential cpp curl flex g++ gcc git-core libaudiofile-dev libc6-dev libdb-dev libexpat1 libgdbm-dev libgnutls-dev libmcrypt-dev libncurses5-dev libnewt-dev libpcre3 libpopt-dev libsctp-dev libsqlite3-dev libtiff4 libtiff4-dev libtool libx11-dev libxml2 libxml2-dev lksctp-tools lynx m4 make mcrypt ncftp nmap openssl sox sqlite3 ssl-cert ssl-cert unixodbc-dev unzip zip zlib1g-dev zlib1g-dev libjpeg-dev libssl-dev sox
        ;;
    'CENTOS')
        yum -y update

        VERS=$(cat /etc/redhat-release |cut -d' ' -f3 |cut -d'.' -f1)

        COMMON_PKGS=" autoconf automake bzip2 cpio curl curl-devel curl-devel expat-devel fileutils gcc-c++ gettext-devel gnutls-devel libjpeg-devel libogg-devel libtiff-devel libtool libvorbis-devel make ncurses-devel nmap openssl openssl-devel openssl-devel perl patch unixODBC unixODBC-devel unzip wget zip zlib zlib-devel bison sox db4 db4-devel gdbm gdbm-devel"
        if [ "$VERS" = "6" ]
        then
            yum -y install $COMMON_PKGS git

        else
            yum -y install $COMMON_PKGS
            #install the RPMFORGE Repository
            if [ ! -f /etc/yum.repos.d/rpmforge.repo ]
            then
                # Install RPMFORGE Repo
                rpm --import http://apt.sw.be/RPM-GPG-KEY.dag.txt
echo '
[rpmforge]
name = Red Hat Enterprise $releasever - RPMforge.net - dag
mirrorlist = http://apt.sw.be/redhat/el5/en/mirrors-rpmforge
enabled = 0
protect = 0
gpgkey = file:///etc/pki/rpm-gpg/RPM-GPG-KEY-rpmforge-dag
gpgcheck = 1
' > /etc/yum.repos.d/rpmforge.repo
            fi
            yum -y --enablerepo=rpmforge install git-core
        fi
        ;;
esac

# Install FreeSWITCH
cd $FS_BASE_PATH
git clone $git_arg $FS_GIT_REPO
cd $FS_BASE_PATH/freeswitch
[ $(uname -m) == "x86_64" ] && enable_64='--enable-64' || enable_64=''
sh bootstrap.sh && ./configure --prefix=$FS_INSTALLED_PATH $enable_64
[ -f modules.conf ] && cp modules.conf modules.conf.bak
sed -i \
-e "s/applications\/mod_sms/#&/g" \
-e "s/dialplans\/mod_dialplan_asterisk/#&/g" \
modules.conf
if $fax_install ; then
    sed -i \
    -e "s/#\(languages\/mod_perl\)/\1/g" \
    modules.conf
else
    sed -i \
    -e "s/#\(applications\/mod_curl\)/\1/g" \
    -e "s/#\(applications\/mod_avmd\)/\1/g" \
    -e "s/#\(asr_tts\/mod_flite\)/\1/g" \
    -e "s/#\(asr_tts\/mod_pocketsphinx\)/\1/g" \
    -e "s/#\(asr_tts\/mod_tts_commandline\)/\1/g" \
    -e "s/#\(formats\/mod_shout\)/formats\/\1/g" \
    -e "s/#\(endpoints\/mod_dingaling\)/\1/g" \
    -e "s/#\(formats\/mod_shell_stream\)/\1/g" \
    -e "s/#\(applications\/mod_soundtouch\)/\1/g" \
    -e "s/#\(say\/mod_say_de\)/\1/g" \
    -e "s/#\(say\/mod_say_es\)/\1/g" \
    -e "s/#\(say\/mod_say_fr\)/\1/g" \
    -e "s/#\(say\/mod_say_it\)/\1/g" \
    -e "s/#\(say\/mod_say_nl\)/\1/g" \
    -e "s/#\(say\/mod_say_ru\)/\1/g" \
    -e "s/#\(say\/mod_say_zh\)/\1/g" \
    -e "s/#\(say\/mod_say_hu\)/\1/g" \
    -e "s/#\(say\/mod_say_th\)/\1/g" \
    modules.conf
fi
make && make install && make sounds-install && make moh-install

# Enable FreeSWITCH modules
cd $FS_INSTALLED_PATH/conf/autoload_configs/
[ -f modules.conf.xml ] && cp modules.conf.xml modules.conf.xml.bak
sed -i \
-e "s/<\!--\s?\(<load module=\"mod_xml_cdr\"\/>\)\s?-->/\1/g" \
-e "s/<load module=\"mod_sms\"\/>/<\!--&-->/g" \
-e "s/<load module=\"mod_dialplan_asterisk\"\/>/<\!--&-->/g" \
modules.conf.xml
if $fax_install ; then
    sed -i \
    -e "s/<\!--\s?\(<load module=\"mod_perl\"\/>\)\s?-->/<load module=\"mod_perl\"\/>/g" \
    modules.conf.xml
else
    sed -i \
    -e "s/<\!--\s?\(<load module=\"mod_dingaling\"\/>\)\s?-->/\1/g" \
    -e "s/<\!--\s?\(<load module=\"mod_shout\"\/>\)\s?-->/\1/g" \
    -e "s/<\!--\s?\(<load module=\"mod_tts_commandline\"\/>\)\s?-->/\1/g" \
    -e "s/<\!--\s?\(<load module=\"mod_flite\"\/>\)\s?-->/\1/g" \
    -e "s/<\!--\s?\(<load module=\"mod_pocketsphinx\"\/>\)\s?-->/\1/g" \
    -e "s/<\!--\s?\(<load module=\"mod_soundtouch\"\/>\)\s?-->/\1/g" \
    -e "s/<\!--\s?\(<load module=\"mod_say_ru\"\/>\)\s?-->/\1/g" \
    -e "s/<\!--\s?\(<load module=\"mod_say_zh\"\/>\)\s?-->/\1/g" \
    -e 's/mod_say_zh.*$/&\n    <load module="mod_say_de"\/>\n    <load module="mod_say_es"\/>\n    <load module="mod_say_fr"\/>\n    <load module="mod_say_it"\/>\n    <load module="mod_say_nl"\/>\n    <load module="mod_say_hu"\/>\n    <load module="mod_say_th"\/>\n    <load module="mod_avmd"\/>/' \
    modules.conf.xml
fi

sed -i \
-e "s/<descriptors>/&\n\n      <X-PRE-PROCESS cmd=\"include\" data=\"..\/voicemail_tones\/*.xml\"\/>/g" \
spandsp.conf.xml

# get the conf file with voicemail beep frequencies
# can be used buy the mod_spandsp detect_tones application
mkdir -p $FS_INSTALLED_PATH/conf/voicemail_tones
cd $FS_INSTALLED_PATH/conf/voicemail_tones

[ -f vm_beeps.xml ] && mv vm_beeps.xml vm_beeps.xml.bak
wget --no-check-certificate $FS_CONF_PATH/conf/vm_beeps.xml -O vm_beeps.xml

# Configure Dialplan
cd $FS_INSTALLED_PATH/conf/dialplan/

# Place Plivo Default Dialplan in FreeSWITCH
[ -f default.xml ] && mv default.xml default.xml.bak
wget --no-check-certificate $FS_CONF_PATH/conf/default.xml -O default.xml

# Place Plivo Public Dialplan in FreeSWITCH
[ -f public.xml ] && mv public.xml public.xml.bak
wget --no-check-certificate $FS_CONF_PATH/conf/public.xml -O public.xml

# Configure Conference @plivo profile
cd $FS_INSTALLED_PATH/conf/autoload_configs/
[ -f conference.conf.xml ] && mv conference.conf.xml conference.conf.xml.bak
wget --no-check-certificate $FS_CONF_PATH/conf/conference.conf.xml -O conference.conf.xml

# move core.db to ramdisk
# TODO: this method below is recommended by FreeSWITCH, but it breaks mod_sofia, mod_voicemail and more
# TODO: either figure out all the config variables that need editing or just stick to the mount method
# sed -i -r \
# -e "s/<\!--\s?<param name=\"core-db-name\" value=\"\/dev\/shm\/core.db\"\s?\/>\s?-->/<param name=\"core-db-name\" value=\"\/dev\/shm\/core.db\" \/>/" \
# switch.conf.xml
echo "tmpfs                   $FS_INSTALLED_PATH/db    tmpfs   defaults    0 0" >> /etc/fstab
mount $FS_INSTALLED_PATH/db

cd $CURRENT_PATH

[ ! -h /etc/freeswitch ] && ln -s "$FS_INSTALLED_PATH/conf" /etc/freeswitch
[ ! -h /var/log/freeswitch ] && ln -s "$FS_INSTALLED_PATH/log" /var/log/freeswitch
[ ! -h /var/run/freeswitch ] && ln -s "$FS_INSTALLED_PATH/run" /var/run/freeswitch
[ ! -d /var/lib/freeswitch ] && mkdir -p /var/lib/freeswitch
[ ! -h /var/lib/freeswitch/scripts ] && ln -s "$FS_INSTALLED_PATH/scripts" /var/lib/freeswitch/scripts

# Install Complete
#clear
echo ""
echo ""
echo ""
echo "**************************************************************"
echo "Congratulations, FreeSWITCH is now installed at '$FS_INSTALLED_PATH'"
echo "**************************************************************"
echo
echo "* To Start FreeSWITCH in foreground :"
echo "    '$FS_INSTALLED_PATH/bin/freeswitch'"
echo
echo "* To Start FreeSWITCH in background :"
echo "    '$FS_INSTALLED_PATH/bin/freeswitch -nc'"
echo
echo "**************************************************************"
echo ""
echo ""
exit 0
