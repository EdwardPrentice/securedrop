import pytest
import re

import testutils

sdvars = testutils.securedrop_test_vars
testinfra_hosts = [sdvars.app_hostname, sdvars.monitor_hostname]


@pytest.mark.parametrize('sysctl_opt', [
  ('net.ipv4.conf.all.accept_redirects', 0),
  ('net.ipv4.conf.all.accept_source_route', 0),
  ('net.ipv4.conf.all.rp_filter', 1),
  ('net.ipv4.conf.all.secure_redirects', 0),
  ('net.ipv4.conf.all.send_redirects', 0),
  ('net.ipv4.conf.default.accept_redirects', 0),
  ('net.ipv4.conf.default.accept_source_route', 0),
  ('net.ipv4.conf.default.rp_filter', 1),
  ('net.ipv4.conf.default.secure_redirects', 0),
  ('net.ipv4.conf.default.send_redirects', 0),
  ('net.ipv4.icmp_echo_ignore_broadcasts', 1),
  ('net.ipv4.ip_forward', 0),
  ('net.ipv4.tcp_max_syn_backlog', 4096),
  ('net.ipv4.tcp_syncookies', 1),
  ('net.ipv6.conf.all.disable_ipv6', 1),
  ('net.ipv6.conf.default.disable_ipv6', 1),
  ('net.ipv6.conf.lo.disable_ipv6', 1),
])
def test_sysctl_options(host, sysctl_opt):
    """
    Ensure sysctl flags are set correctly. Most of these checks
    are disabling IPv6 and hardening IPv4, which is appropriate
    due to the heavy use of Tor.
    """
    with host.sudo():
        # For Focal, we disable IPv6 entirely, so the IPv6 sysctl options won't exist
        if sysctl_opt[0].startswith("net.ipv6") and host.system_info.codename == "focal":
            return True
        assert host.sysctl(sysctl_opt[0]) == sysctl_opt[1]


def test_dns_setting(host):
    """
    Ensure DNS service is hard-coded in resolv.conf config.
    """
    if host.system_info.codename == "focal":
        fpath = "/etc/resolv.conf"
    else:
        fpath = "/etc/resolvconf/resolv.conf.d/base"
    f = host.file(fpath)
    assert f.is_file
    assert f.user == "root"
    assert f.group == "root"
    assert f.mode == 0o644
    assert f.contains(r'^nameserver 8\.8\.8\.8$')


@pytest.mark.parametrize('kernel_module', [
    'bluetooth',
    'iwlwifi',
])
def test_blacklisted_kernel_modules(host, kernel_module):
    """
    Test that unwanted kernel modules are blacklisted on the system.
    Mostly these checks are defense-in-depth approaches to ensuring
    that wireless interfaces will not work.
    """
    with host.sudo():
        c = host.run("lsmod")
        assert kernel_module not in c.stdout

    f = host.file("/etc/modprobe.d/blacklist.conf")
    assert f.contains("^blacklist {}$".format(kernel_module))


def test_swap_disabled(host):
    """
    Ensure swap space is disabled. Prohibit writing memory to swapfiles
    to reduce the threat of forensic analysis leaking any sensitive info.
    """
    hostname = host.check_output('hostname')

    # Mon doesn't have swap disabled yet
    if hostname.startswith('mon'):
        return True

    c = host.check_output('swapon --summary')
    # A leading slash will indicate full path to a swapfile.
    assert not re.search("^/", c, re.M)

    # On Xenial, swapon 2.27.1 shows blank output, with no headers, so
    # check for empty output as confirmation of no swap.
    rgx = re.compile("^$")

    assert re.search(rgx, c)


def test_twofactor_disabled_on_tty(host):
    """
    Having 2FA on TTY logins is cumbersome on systems without encrypted drives.
    Let's make sure this option is disabled!
    """

    pam_auth_file = host.file("/etc/pam.d/common-auth").content_string

    assert "auth required pam_google_authenticator.so" not in pam_auth_file
    assert "pam_ecryptfs.so unwrap" not in pam_auth_file


@pytest.mark.parametrize('sshd_opts', [
  ('UsePAM', 'no'),
  ('ChallengeResponseAuthentication', 'no'),
  ('PasswordAuthentication', 'no'),
  ('PubkeyAuthentication', 'yes'),
  ('RSAAuthentication', 'yes'),
  ('AllowGroups', 'ssh'),
  ('AllowTcpForwarding', 'no'),
  ('AllowAgentForwarding', 'no'),
  ('PermitTunnel', 'no'),
  ('X11Forwarding', 'no'),
])
def test_sshd_config(host, sshd_opts):
    """
    Let's ensure sshd does not fall back to password-based authentication
    """

    sshd_config_file = host.file("/etc/ssh/sshd_config").content_string

    line = "{} {}".format(sshd_opts[0], sshd_opts[1])
    assert line in sshd_config_file


@pytest.mark.parametrize('logfile', [
    '/var/log/auth.log',
    '/var/log/syslog',
])
def test_no_ecrypt_messages_in_logs(host, logfile):
    """
    Ensure pam_ecryptfs is removed from /etc/pam.d/common-auth : not only is
    no longer needed, it causes error messages (see issue #3963)
    """
    error_message = "pam_ecryptfs.so: cannot open shared object file"
    with host.sudo():
        f = host.file(logfile)
        # Not using `f.contains(<pattern>)` because that'd cause the sought
        # string to make it into syslog as a side-effect of the testinfra
        # invocation, causing subsequent test runs to report failure.
        assert error_message not in f.content_string


@pytest.mark.parametrize('package', [
    'aptitude',
    'cloud-init',
    'libiw30',
    'python-is-python2',
    'snapd',
    'torsocks',
    'wireless-tools',
    'wpasupplicant',
])
def test_unused_packages_are_removed(host, package):
    """ Check if unused package is present """
    assert host.package(package).is_installed is False


def test_iptables_packages(host):
    """
    Focal hosts should use iptables-persistent for enforcing
    firewall config across reboots.
    """
    if host.system_info.codename == "focal":
        assert host.package("iptables-persistent").is_installed
    else:
        assert not host.package("iptables-persistent").is_installed


def test_snapd_absent(host):
    assert not host.file("/lib/systemd/system/snapd.service").exists
    assert not host.file("/etc/apparmor.d/usr.lib.snapd.snap-confine.real").exists
    assert not host.file("/usr/bin/snap").exists
    assert not host.file("/var/lib/snapd/snaps").exists
