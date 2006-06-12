#!/usr/bin/env python

# Display information about package(s) (suite, version, etc.)
# Copyright (C) 2000, 2001, 2002, 2003, 2004, 2005, 2006  James Troup <james@nocrew.org>

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

################################################################################

# <aj> ooo, elmo has "special powers"
# <neuro> ooo, does he have lasers that shoot out of his eyes?
# <aj> dunno
# <aj> maybe he can turn invisible? that'd sure help with improved transparency!

################################################################################

import os, pg, sys
import apt_pkg
import daklib.database
import daklib.utils

################################################################################

Cnf = None
projectB = None

################################################################################

def usage (exit_code=0):
    print """Usage: dak ls [OPTION] PACKAGE[...]
Display information about PACKAGE(s).

  -a, --architecture=ARCH    only show info for ARCH(s)
  -b, --binary-type=TYPE     only show info for binary TYPE
  -c, --component=COMPONENT  only show info for COMPONENT(s)
  -g, --greaterorequal       show buildd 'dep-wait pkg >= {highest version}' info
  -G, --greaterthan          show buildd 'dep-wait pkg >> {highest version}' info
  -h, --help                 show this help and exit
  -r, --regex                treat PACKAGE as a regex
  -s, --suite=SUITE          only show info for this suite
  -S, --source-and-binary    show info for the binary children of source pkgs

ARCH, COMPONENT and SUITE can be comma (or space) separated lists, e.g.
    --architecture=m68k,i386"""
    sys.exit(exit_code)

################################################################################

def main ():
    global Cnf, projectB

    Cnf = daklib.utils.get_conf()

    Arguments = [('a', "architecture", "Ls::Options::Architecture", "HasArg"),
                 ('b', "binarytype", "Ls::Options::BinaryType", "HasArg"),
                 ('c', "component", "Ls::Options::Component", "HasArg"),
                 ('f', "format", "Ls::Options::Format", "HasArg"),
                 ('g', "greaterorequal", "Ls::Options::GreaterOrEqual"),
                 ('G', "greaterthan", "Ls::Options::GreaterThan"),
                 ('r', "regex", "Ls::Options::Regex"),
                 ('s', "suite", "Ls::Options::Suite", "HasArg"),
                 ('S', "source-and-binary", "Ls::Options::Source-And-Binary"),
                 ('h', "help", "Ls::Options::Help")]
    for i in [ "architecture", "binarytype", "component", "format",
               "greaterorequal", "greaterthan", "regex", "suite",
               "source-and-binary", "help" ]:
	if not Cnf.has_key("Ls::Options::%s" % (i)):
	    Cnf["Ls::Options::%s" % (i)] = ""

    packages = apt_pkg.ParseCommandLine(Cnf,Arguments,sys.argv)
    Options = Cnf.SubTree("Ls::Options")

    if Options["Help"]:
        usage()
    if not packages:
        daklib.utils.fubar("need at least one package name as an argument.")

    projectB = pg.connect(Cnf["DB::Name"], Cnf["DB::Host"], int(Cnf["DB::Port"]))
    daklib.database.init(Cnf, projectB)

    # If cron.daily is running; warn the user that our output might seem strange
    if os.path.exists(os.path.join(Cnf["Dir::Root"], "Archive_Maintenance_In_Progress")):
        daklib.utils.warn("Archive maintenance is in progress; database inconsistencies are possible.")

    # Handle buildd maintenance helper options
    if Options["GreaterOrEqual"] or Options["GreaterThan"]:
        if Options["GreaterOrEqual"] and Options["GreaterThan"]:
            daklib.utils.fubar("-g/--greaterorequal and -G/--greaterthan are mutually exclusive.")
        if not Options["Suite"]:
            Options["Suite"] = "unstable"

    # Parse -a/--architecture, -c/--component and -s/--suite
    (con_suites, con_architectures, con_components, check_source) = \
                 daklib.utils.parse_args(Options)

    if Options["BinaryType"]:
        if Options["BinaryType"] != "udeb" and Options["BinaryType"] != "deb":
            daklib.utils.fubar("Invalid binary type.  'udeb' and 'deb' recognised.")
        con_bintype = "AND b.type = '%s'" % (Options["BinaryType"])
        # REMOVE ME TRAMP
        if Options["BinaryType"] == "udeb":
            check_source = 0
    else:
        con_bintype = ""

    if Options["Regex"]:
        comparison_operator = "~"
    else:
        comparison_operator = "="

    if Options["Source-And-Binary"]:
        new_packages = []
        for package in packages:
            q = projectB.query("SELECT DISTINCT b.package FROM binaries b, bin_associations ba, suite su, source s WHERE b.source = s.id AND su.id = ba.suite AND b.id = ba.bin AND s.source %s '%s' %s" % (comparison_operator, package, con_suites))
            new_packages.extend([ i[0] for i in q.getresult() ])
            if package not in new_packages:
                new_packages.append(package)
        packages = new_packages

    results = 0
    for package in packages:
        q = projectB.query("""
SELECT b.package, b.version, a.arch_string, su.suite_name, c.name, m.name
  FROM binaries b, architecture a, suite su, bin_associations ba,
       files f, location l, component c, maintainer m
 WHERE b.package %s '%s' AND a.id = b.architecture AND su.id = ba.suite
   AND b.id = ba.bin AND b.file = f.id AND f.location = l.id
   AND l.component = c.id AND b.maintainer = m.id %s %s %s
""" % (comparison_operator, package, con_suites, con_architectures, con_bintype))
        ql = q.getresult()
        if check_source:
            q = projectB.query("""
SELECT s.source, s.version, 'source', su.suite_name, c.name, m.name
  FROM source s, suite su, src_associations sa, files f, location l,
       component c, maintainer m
 WHERE s.source %s '%s' AND su.id = sa.suite AND s.id = sa.source
   AND s.file = f.id AND f.location = l.id AND l.component = c.id
   AND s.maintainer = m.id %s
""" % (comparison_operator, package, con_suites))
            ql.extend(q.getresult())
        d = {}
        highver = {}
        for i in ql:
            results += 1
            (pkg, version, architecture, suite, component, maintainer) = i
            if component != "main":
                suite = "%s/%s" % (suite, component)
            if not d.has_key(pkg):
                d[pkg] = {}
            highver.setdefault(pkg,"")
            if not d[pkg].has_key(version):
                d[pkg][version] = {}
                if apt_pkg.VersionCompare(version, highver[pkg]) > 0:
                    highver[pkg] = version
            if not d[pkg][version].has_key(suite):
                d[pkg][version][suite] = []
            d[pkg][version][suite].append(architecture)

        packages = d.keys()
        packages.sort()
        for pkg in packages:
            versions = d[pkg].keys()
            versions.sort(apt_pkg.VersionCompare)
            for version in versions:
                suites = d[pkg][version].keys()
                suites.sort()
                for suite in suites:
                    arches = d[pkg][version][suite]
                    arches.sort(daklib.utils.arch_compare_sw)
                    if Options["Format"] == "": #normal
                        sys.stdout.write("%10s | %10s | %13s | " % (pkg, version, suite))
                        sys.stdout.write(", ".join(arches))
                        sys.stdout.write('\n')
                    elif Options["Format"] in [ "control-suite", "heidi" ]:
                        for arch in arches:
                            sys.stdout.write("%s %s %s\n" % (pkg, version, arch))
            if Options["GreaterOrEqual"]:
                print "\n%s (>= %s)" % (pkg, highver[pkg])
            if Options["GreaterThan"]:
                print "\n%s (>> %s)" % (pkg, highver[pkg])

    if not results:
        sys.exit(1)

#######################################################################################

if __name__ == '__main__':
    main()
