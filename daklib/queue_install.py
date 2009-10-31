#!/usr/bin/env python
# vim:set et sw=4:

"""
Utility functions for process-upload

@contact: Debian FTP Master <ftpmaster@debian.org>
@copyright: 2000, 2001, 2002, 2003, 2004, 2005, 2006  James Troup <james@nocrew.org>
@copyright: 2009  Joerg Jaspert <joerg@debian.org>
@copyright: 2009  Mark Hymers <mhy@debian.org>
@license: GNU General Public License version 2 or later
"""

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

import os

from daklib import utils
from daklib.dbconn import *
from daklib.config import Config

################################################################################

def package_to_suite(u, suite_name, session):
    if not u.pkg.changes["distribution"].has_key(suite_name):
        return False

    ret = True

    if not u.pkg.changes["architecture"].has_key("source"):
        q = session.query(SrcAssociation.sa_id)
        q = q.join(Suite).filter_by(suite_name=suite_name)
        q = q.join(DBSource).filter_by(source=u.pkg.changes['source'])
        q = q.filter_by(version=u.pkg.changes['version']).limit(1)

        # NB: Careful, this logic isn't what you would think it is
        # Source is already in the target suite so no need to go to policy
        # Instead, we don't move to the policy area, we just do an ACCEPT
        if q.count() > 0:
            ret = False

    return ret

def package_to_queue(u, summary, short_summary, queue, perms=0660, announce=None):
    cnf = Config()
    dir = queue.path

    print "Moving to %s policy queue" % queue.queue_name.upper()
    u.logger.log(["Moving to %s" % queue.queue_name, u.pkg.changes_file])

    u.move_to_dir(dir, perms=perms)
    # TODO: Put building logic in here?  We used to take a build=bool argument

    # Check for override disparities
    u.check_override()

    # Send accept mail, announce to lists and close bugs
    if announce and not cnf["Dinstall::Options::No-Mail"]:
        template = os.path.join(cnf["Dir::Templates"], announce)
        u.update_subst()
        u.Subst["__SUITE__"] = ""
        mail_message = utils.TemplateSubst(u.Subst, template)
        utils.send_mail(mail_message)
        u.announce(short_summary, True)

################################################################################

# TODO: This logic needs to be replaced with policy queues before we upgrade
# security master

#def is_unembargo(u):
#    session = DBConn().session()
#    cnf = Config()
#
#    q = session.execute("SELECT package FROM disembargo WHERE package = :source AND version = :version", u.pkg.changes)
#    if q.rowcount > 0:
#        session.close()
#        return True
#
#    oldcwd = os.getcwd()
#    os.chdir(cnf["Dir::Queue::Disembargo"])
#    disdir = os.getcwd()
#    os.chdir(oldcwd)
#
#    ret = False
#
#    if u.pkg.directory == disdir:
#        if u.pkg.changes["architecture"].has_key("source"):
#            session.execute("INSERT INTO disembargo (package, version) VALUES (:package, :version)", u.pkg.changes)
#            session.commit()
#
#            ret = True
#
#    session.close()
#
#    return ret
#
#def queue_unembargo(u, summary, short_summary, session=None):
#    return package_to_queue(u, summary, short_summary, "Unembargoed",
#                            perms=0660, build=True, announce='process-unchecked.accepted')
#
#################################################################################
#
#def is_embargo(u):
#    # if embargoed queues are enabled always embargo
#    return True
#
#def queue_embargo(u, summary, short_summary, session=None):
#    return package_to_queue(u, summary, short_summary, "Unembargoed",
#                            perms=0660, build=True, announce='process-unchecked.accepted')

################################################################################

def is_autobyhand(u):
    cnf = Config()

    all_auto = 1
    any_auto = 0
    for f in u.pkg.files.keys():
        if u.pkg.files[f].has_key("byhand"):
            any_auto = 1

            # filename is of form "PKG_VER_ARCH.EXT" where PKG, VER and ARCH
            # don't contain underscores, and ARCH doesn't contain dots.
            # further VER matches the .changes Version:, and ARCH should be in
            # the .changes Architecture: list.
            if f.count("_") < 2:
                all_auto = 0
                continue

            (pckg, ver, archext) = f.split("_", 2)
            if archext.count(".") < 1 or u.pkg.changes["version"] != ver:
                all_auto = 0
                continue

            ABH = cnf.SubTree("AutomaticByHandPackages")
            if not ABH.has_key(pckg) or \
              ABH["%s::Source" % (pckg)] != u.pkg.changes["source"]:
                print "not match %s %s" % (pckg, u.pkg.changes["source"])
                all_auto = 0
                continue

            (arch, ext) = archext.split(".", 1)
            if arch not in u.pkg.changes["architecture"]:
                all_auto = 0
                continue

            u.pkg.files[f]["byhand-arch"] = arch
            u.pkg.files[f]["byhand-script"] = ABH["%s::Script" % (pckg)]

    return any_auto and all_auto

def do_autobyhand(u, summary, short_summary, session=None):
    print "Attempting AUTOBYHAND."
    byhandleft = True
    for f, entry in u.pkg.files.items():
        byhandfile = f

        if not entry.has_key("byhand"):
            continue

        if not entry.has_key("byhand-script"):
            byhandleft = True
            continue

        os.system("ls -l %s" % byhandfile)

        result = os.system("%s %s %s %s %s" % (
                entry["byhand-script"],
                byhandfile,
                u.pkg.changes["version"],
                entry["byhand-arch"],
                os.path.abspath(u.pkg.changes_file)))

        if result == 0:
            os.unlink(byhandfile)
            del entry
        else:
            print "Error processing %s, left as byhand." % (f)
            byhandleft = True

    if byhandleft:
        do_byhand(u, summary, short_summary, session)
    else:
        u.accept(summary, short_summary, session)
        u.check_override()

################################################################################

def is_byhand(u):
    for f in u.pkg.files.keys():
        if u.pkg.files[f].has_key("byhand"):
            return True
    return False

def do_byhand(u, summary, short_summary, session=None):
    return package_to_queue(u, summary, short_summary, "Byhand",
                            perms=0660, build=False, announce=None)

################################################################################

def is_new(u):
    for f in u.pkg.files.keys():
        if u.pkg.files[f].has_key("new"):
            return True
    return False

def acknowledge_new(u, summary, short_summary, session=None):
    cnf = Config()

    print "Moving to NEW queue."
    u.logger.log(["Moving to new", u.pkg.changes_file])

    u.move_to_dir(cnf["Dir::Queue::New"], perms=0640, changesperms=0644)

    if not cnf["Dinstall::Options::No-Mail"]:
        print "Sending new ack."
        template = os.path.join(cnf["Dir::Templates"], 'process-unchecked.new')
        u.update_subst()
        u.Subst["__SUMMARY__"] = summary
        new_ack_message = utils.TemplateSubst(u.Subst, template)
        utils.send_mail(new_ack_message)

################################################################################

# q-unapproved hax0ring
QueueInfo = {
    "new": { "is": is_new, "process": acknowledge_new },
    "autobyhand" : { "is" : is_autobyhand, "process": do_autobyhand },
    "byhand" : { "is": is_byhand, "process": do_byhand },
}

def determine_target(u):
    cnf = Config()

    # Statically handled queues
    target = None

    for q in QueueInfo.keys():
        if QueueInfo[q]["is"](u):
            target = q

    return target

###############################################################################

