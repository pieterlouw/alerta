%{!?_with_teamcity: %define version 4.1.0}
%{!?_with_teamcity: %define release 1}

Name: alerta-server
Summary: Alerta monitoring system
Version: %{version}
Release: %{release}
Source0: alerta-%{version}.tar.gz
License: Apache License 2.0
Group: Utilities/System
BuildRoot: %{_tmppath}/alerta-%{version}-%{release}-buildroot
Prefix: /opt
BuildArch: x86_64
Vendor: Nick Satterly <nick.satterly@theguardian.com>
Url: https://github.com/guardian/alerta
BuildRequires: python-devel, python-setuptools, python-virtualenv
Requires: httpd, mod_wsgi

%description
Alerta is a monitoring system that consolidates alerts from
multiple sources like syslog, SNMP, Nagios, Riemann, AWS
CloudWatch and Pingdom, and displays them on an alert console.

%prep
%setup -n alerta-%{version}

%build
/usr/bin/virtualenv alerta
alerta/bin/pip install -r requirements.txt --upgrade
alerta/bin/python setup.py install --single-version-externally-managed --root=/
/usr/bin/virtualenv --relocatable alerta

%install
%__mkdir_p %{buildroot}/opt/alerta/bin
cp %{_builddir}/alerta-%{version}/alerta/bin/alert* %{buildroot}/opt/alerta/bin/
cp %{_builddir}/alerta-%{version}/alerta/bin/python* %{buildroot}/opt/alerta/bin/
cp %{_builddir}/alerta-%{version}/alerta/bin/activate* %{buildroot}/opt/alerta/bin/
cp -r %{_builddir}/alerta-%{version}/alerta/lib %{buildroot}/opt/alerta/

%__mkdir_p %{buildroot}/var/run/wsgi
%__mkdir_p %{buildroot}%{_sysconfdir}/httpd/conf.d/
cat > %{buildroot}%{_sysconfdir}/httpd/conf.d/alerta.conf << EOF
Listen 8080
WSGISocketPrefix /var/run/wsgi
<VirtualHost *:8080>
  ServerName localhost
  WSGIDaemonProcess alerta processes=5 threads=5
  WSGIProcessGroup alerta
  WSGIScriptAlias / /var/www/api.wsgi
  WSGIPassAuthorization On
  ErrorLog logs/alerta-error_log
  CustomLog logs/alerta-access_log combined
</VirtualHost>
EOF

%__mkdir_p %{buildroot}/var/www
cat > %{buildroot}/var/www/api.wsgi << EOF
#!/usr/bin/env python
activate_this = '/opt/alerta/bin/activate_this.py'
execfile(activate_this, dict(__file__=activate_this))
from alerta.app import app as application
EOF

%__mkdir_p %{buildroot}%{_sysconfdir}/profile.d/
cat > %{buildroot}%{_sysconfdir}/profile.d/alerta.sh << EOF
# Set path for Alerta command line tool
export PATH=$PATH:/opt/alerta/bin
# Uncomment this line to specify an API Key
#export ALERTA_API_KEY=
EOF

if [ -n "$(type -p prelink)" ]; then
    prelink -u %{buildroot}/opt/alerta/bin/python*
fi

%clean
rm -rf %{buildroot}

%files
%defattr(-,root,root)
%config(noreplace) %{_sysconfdir}/httpd/conf.d/alerta.conf
%config(noreplace) %{_sysconfdir}/profile.d/alerta.sh
%defattr(-,alerta,alerta)
/opt/alerta/bin/alerta
/opt/alerta/bin/alertad
%config(noreplace) /var/www/api.wsgi
/opt/alerta/bin/python*
/opt/alerta/bin/activate*
/opt/alerta/lib/*
/var/run/wsgi

%pre
getent group alerta >/dev/null || groupadd -r alerta
getent passwd alerta >/dev/null || \
    useradd -r -g alerta -d /var/lib/alerta -s /sbin/nologin \
    -c "Alerta monitoring system" alerta
exit 0

%changelog
* Thu Mar 12 2015 Nick Satterly <nick.satterly@theguardian.com> - 4.1.0-1
- Update RPM SPEC file for Release 4.1