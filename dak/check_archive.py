#!/usr/bin/env python

# Various different sanity checks
# Copyright (C) 2000, 2001, 2002, 2003, 2004, 2006  James Troup <james@nocrew.org>

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

#   And, lo, a great and menacing voice rose from the depths, and with
#   great wrath and vehemence it's voice boomed across the
#   land... ``hehehehehehe... that *tickles*''
#                                                       -- aj on IRC

################################################################################

import commands, os, pg, stat, sys, time
import apt_pkg, apt_inst
import daklib.database
import daklib.utils

################################################################################

Cnf = None
projectB = None
db_files = {}
waste = 0.0
excluded = {}
current_file = None
future_files = {}
current_time = time.time()

################################################################################

def usage(exit_code=0):
    print """Usage: dak check-archive MODE
Run various sanity checks of the archive and/or database.

  -h, --help                show this help and exit.

The following MODEs are available:

  md5sums            - validate the md5sums stored in the database
  files              - check files in the database against what's in the archive
  dsc-syntax         - validate the syntax of .dsc files in the archive
  missing-overrides  - check for missing overrides
  source-in-one-dir  - ensure the source for each package is in one directory
  timestamps         - check for future timestamps in .deb's
  tar-gz-in-dsc      - ensure each .dsc lists a .tar.gz file
  validate-indices   - ensure files mentioned in Packages & Sources exist
  files-not-symlinks - check files in the database aren't symlinks
  validate-builddeps - validate build-dependencies of .dsc files in the archive
"""
    sys.exit(exit_code)

################################################################################

def process_dir (unused, dirname, filenames):
    global waste, db_files, excluded

    if dirname.find('/disks-') != -1 or dirname.find('upgrade-') != -1:
        return
    # hack; can't handle .changes files
    if dirname.find('proposed-updates') != -1:
        return
    for name in filenames:
        filename = os.path.abspath(dirname+'/'+name)
        filename = filename.replace('potato-proposed-updates', 'proposed-updates')
        if os.path.isfile(filename) and not os.path.islink(filename) and not db_files.has_key(filename) and not excluded.has_key(filename):
            waste += os.stat(filename)[stat.ST_SIZE]
            print filename

################################################################################

def check_files():
    global db_files

    print "Building list of database files..."
    q = projectB.query("SELECT l.path, f.filename FROM files f, location l WHERE f.location = l.id")
    ql = q.getresult()

    db_files.clear()
    for i in ql:
	filename = os.path.abspath(i[0] + i[1])
        db_files[filename] = ""
        if os.access(filename, os.R_OK) == 0:
            daklib.utils.warn("'%s' doesn't exist." % (filename))

    filename = Cnf["Dir::Override"]+'override.unreferenced'
    if os.path.exists(filename):
        file = daklib.utils.open_file(filename)
        for filename in file.readlines():
            filename = filename[:-1]
            excluded[filename] = ""

    print "Checking against existent files..."

    os.path.walk(Cnf["Dir::Root"]+'pool/', process_dir, None)

    print
    print "%s wasted..." % (daklib.utils.size_type(waste))

################################################################################

def check_dscs():
    count = 0
    suite = 'unstable'
    for component in Cnf.SubTree("Component").List():
        if component == "mixed":
            continue
        component = component.lower()
        list_filename = '%s%s_%s_source.list' % (Cnf["Dir::Lists"], suite, component)
        list_file = daklib.utils.open_file(list_filename)
        for line in list_file.readlines():
            file = line[:-1]
            try:
                daklib.utils.parse_changes(file, signing_rules=1)
            except daklib.utils.invalid_dsc_format_exc, line:
                daklib.utils.warn("syntax error in .dsc file '%s', line %s." % (file, line))
                count += 1

    if count:
        daklib.utils.warn("Found %s invalid .dsc files." % (count))

################################################################################

def check_override():
    for suite in [ "stable", "unstable" ]:
        print suite
        print "-"*len(suite)
        print
        suite_id = daklib.database.get_suite_id(suite)
        q = projectB.query("""
SELECT DISTINCT b.package FROM binaries b, bin_associations ba
 WHERE b.id = ba.bin AND ba.suite = %s AND NOT EXISTS
       (SELECT 1 FROM override o WHERE o.suite = %s AND o.package = b.package)"""
                           % (suite_id, suite_id))
        print q
        q = projectB.query("""
SELECT DISTINCT s.source FROM source s, src_associations sa
  WHERE s.id = sa.source AND sa.suite = %s AND NOT EXISTS
       (SELECT 1 FROM override o WHERE o.suite = %s and o.package = s.source)"""
                           % (suite_id, suite_id))
        print q

################################################################################

# Ensure that the source files for any given package is all in one
# directory so that 'apt-get source' works...

def check_source_in_one_dir():
    # Not the most enterprising method, but hey...
    broken_count = 0
    q = projectB.query("SELECT id FROM source;")
    for i in q.getresult():
        source_id = i[0]
        q2 = projectB.query("""
SELECT l.path, f.filename FROM files f, dsc_files df, location l WHERE df.source = %s AND f.id = df.file AND l.id = f.location"""
                            % (source_id))
        first_path = ""
        first_filename = ""
        broken = 0
        for j in q2.getresult():
            filename = j[0] + j[1]
            path = os.path.dirname(filename)
            if first_path == "":
                first_path = path
                first_filename = filename
            elif first_path != path:
                symlink = path + '/' + os.path.basename(first_filename)
                if not os.path.exists(symlink):
                    broken = 1
                    print "WOAH, we got a live one here... %s [%s] {%s}" % (filename, source_id, symlink)
        if broken:
            broken_count += 1
    print "Found %d source packages where the source is not all in one directory." % (broken_count)

################################################################################

def check_md5sums():
    print "Getting file information from database..."
    q = projectB.query("SELECT l.path, f.filename, f.md5sum, f.size FROM files f, location l WHERE f.location = l.id")
    ql = q.getresult()

    print "Checking file md5sums & sizes..."
    for i in ql:
	filename = os.path.abspath(i[0] + i[1])
        db_md5sum = i[2]
        db_size = int(i[3])
        try:
            file = daklib.utils.open_file(filename)
        except:
            daklib.utils.warn("can't open '%s'." % (filename))
            continue
        md5sum = apt_pkg.md5sum(file)
        size = os.stat(filename)[stat.ST_SIZE]
        if md5sum != db_md5sum:
            daklib.utils.warn("**WARNING** md5sum mismatch for '%s' ('%s' [current] vs. '%s' [db])." % (filename, md5sum, db_md5sum))
        if size != db_size:
            daklib.utils.warn("**WARNING** size mismatch for '%s' ('%s' [current] vs. '%s' [db])." % (filename, size, db_size))

    print "Done."

################################################################################
#
# Check all files for timestamps in the future; common from hardware
# (e.g. alpha) which have far-future dates as their default dates.

def Ent(Kind,Name,Link,Mode,UID,GID,Size,MTime,Major,Minor):
    global future_files

    if MTime > current_time:
        future_files[current_file] = MTime
        print "%s: %s '%s','%s',%u,%u,%u,%u,%u,%u,%u" % (current_file, Kind,Name,Link,Mode,UID,GID,Size, MTime, Major, Minor)

def check_timestamps():
    global current_file

    q = projectB.query("SELECT l.path, f.filename FROM files f, location l WHERE f.location = l.id AND f.filename ~ '.deb$'")
    ql = q.getresult()
    db_files.clear()
    count = 0
    for i in ql:
	filename = os.path.abspath(i[0] + i[1])
        if os.access(filename, os.R_OK):
            file = daklib.utils.open_file(filename)
            current_file = filename
            sys.stderr.write("Processing %s.\n" % (filename))
            apt_inst.debExtract(file,Ent,"control.tar.gz")
            file.seek(0)
            apt_inst.debExtract(file,Ent,"data.tar.gz")
            count += 1
    print "Checked %d files (out of %d)." % (count, len(db_files.keys()))

################################################################################

def check_missing_tar_gz_in_dsc():
    count = 0

    print "Building list of database files..."
    q = projectB.query("SELECT l.path, f.filename FROM files f, location l WHERE f.location = l.id AND f.filename ~ '.dsc$'")
    ql = q.getresult()
    if ql:
        print "Checking %d files..." % len(ql)
    else:
        print "No files to check."
    for i in ql:
        filename = os.path.abspath(i[0] + i[1])
        try:
            # NB: don't enforce .dsc syntax
            dsc = daklib.utils.parse_changes(filename)
        except:
            daklib.utils.fubar("error parsing .dsc file '%s'." % (filename))
        dsc_files = daklib.utils.build_file_list(dsc, is_a_dsc=1)
        has_tar = 0
        for file in dsc_files.keys():
            m = daklib.utils.re_issource.match(file)
            if not m:
                daklib.utils.fubar("%s not recognised as source." % (file))
            type = m.group(3)
            if type == "orig.tar.gz" or type == "tar.gz":
                has_tar = 1
        if not has_tar:
            daklib.utils.warn("%s has no .tar.gz in the .dsc file." % (file))
            count += 1

    if count:
        daklib.utils.warn("Found %s invalid .dsc files." % (count))


################################################################################

def validate_sources(suite, component):
    filename = "%s/dists/%s/%s/source/Sources.gz" % (Cnf["Dir::Root"], suite, component)
    print "Processing %s..." % (filename)
    # apt_pkg.ParseTagFile needs a real file handle and can't handle a GzipFile instance...
    temp_filename = daklib.utils.temp_filename()
    (result, output) = commands.getstatusoutput("gunzip -c %s > %s" % (filename, temp_filename))
    if (result != 0):
        sys.stderr.write("Gunzip invocation failed!\n%s\n" % (output))
        sys.exit(result)
    sources = daklib.utils.open_file(temp_filename)
    Sources = apt_pkg.ParseTagFile(sources)
    while Sources.Step():
        source = Sources.Section.Find('Package')
        directory = Sources.Section.Find('Directory')
        files = Sources.Section.Find('Files')
        for i in files.split('\n'):
            (md5, size, name) = i.split()
            filename = "%s/%s/%s" % (Cnf["Dir::Root"], directory, name)
            if not os.path.exists(filename):
                if directory.find("potato") == -1:
                    print "W: %s missing." % (filename)
                else:
                    pool_location = daklib.utils.poolify (source, component)
                    pool_filename = "%s/%s/%s" % (Cnf["Dir::Pool"], pool_location, name)
                    if not os.path.exists(pool_filename):
                        print "E: %s missing (%s)." % (filename, pool_filename)
                    else:
                        # Create symlink
                        pool_filename = os.path.normpath(pool_filename)
                        filename = os.path.normpath(filename)
                        src = daklib.utils.clean_symlink(pool_filename, filename, Cnf["Dir::Root"])
                        print "Symlinking: %s -> %s" % (filename, src)
                        #os.symlink(src, filename)
    sources.close()
    os.unlink(temp_filename)

########################################

def validate_packages(suite, component, architecture):
    filename = "%s/dists/%s/%s/binary-%s/Packages.gz" \
               % (Cnf["Dir::Root"], suite, component, architecture)
    print "Processing %s..." % (filename)
    # apt_pkg.ParseTagFile needs a real file handle and can't handle a GzipFile instance...
    temp_filename = daklib.utils.temp_filename()
    (result, output) = commands.getstatusoutput("gunzip -c %s > %s" % (filename, temp_filename))
    if (result != 0):
        sys.stderr.write("Gunzip invocation failed!\n%s\n" % (output))
        sys.exit(result)
    packages = daklib.utils.open_file(temp_filename)
    Packages = apt_pkg.ParseTagFile(packages)
    while Packages.Step():
        filename = "%s/%s" % (Cnf["Dir::Root"], Packages.Section.Find('Filename'))
        if not os.path.exists(filename):
            print "W: %s missing." % (filename)
    packages.close()
    os.unlink(temp_filename)

########################################

def check_indices_files_exist():
    for suite in [ "stable", "testing", "unstable" ]:
        for component in Cnf.ValueList("Suite::%s::Components" % (suite)):
            architectures = Cnf.ValueList("Suite::%s::Architectures" % (suite))
            for arch in [ i.lower() for i in architectures ]:
                if arch == "source":
                    validate_sources(suite, component)
                elif arch == "all":
                    continue
                else:
                    validate_packages(suite, component, arch)

################################################################################

def check_files_not_symlinks():
    print "Building list of database files... ",
    before = time.time()
    q = projectB.query("SELECT l.path, f.filename, f.id FROM files f, location l WHERE f.location = l.id")
    print "done. (%d seconds)" % (int(time.time()-before))
    q_files = q.getresult()

#      locations = {}
#      q = projectB.query("SELECT l.path, c.name, l.id FROM location l, component c WHERE l.component = c.id")
#      for i in q.getresult():
#          path = os.path.normpath(i[0] + i[1])
#          locations[path] = (i[0], i[2])

#      q = projectB.query("BEGIN WORK")
    for i in q_files:
	filename = os.path.normpath(i[0] + i[1])
#        file_id = i[2]
        if os.access(filename, os.R_OK) == 0:
            daklib.utils.warn("%s: doesn't exist." % (filename))
        else:
            if os.path.islink(filename):
                daklib.utils.warn("%s: is a symlink." % (filename))
                # You probably don't want to use the rest of this...
#                  print "%s: is a symlink." % (filename)
#                  dest = os.readlink(filename)
#                  if not os.path.isabs(dest):
#                      dest = os.path.normpath(os.path.join(os.path.dirname(filename), dest))
#                  print "--> %s" % (dest)
#                  # Determine suitable location ID
#                  # [in what must be the suckiest way possible?]
#                  location_id = None
#                  for path in locations.keys():
#                      if dest.find(path) == 0:
#                          (location, location_id) = locations[path]
#                          break
#                  if not location_id:
#                      daklib.utils.fubar("Can't find location for %s (%s)." % (dest, filename))
#                  new_filename = dest.replace(location, "")
#                  q = projectB.query("UPDATE files SET filename = '%s', location = %s WHERE id = %s" % (new_filename, location_id, file_id))
#      q = projectB.query("COMMIT WORK")

################################################################################

def chk_bd_process_dir (unused, dirname, filenames):
    for name in filenames:
        if not name.endswith(".dsc"):
            continue
        filename = os.path.abspath(dirname+'/'+name)
        dsc = daklib.utils.parse_changes(filename)
        for field_name in [ "build-depends", "build-depends-indep" ]:
            field = dsc.get(field_name)
            if field:
                try:
                    apt_pkg.ParseSrcDepends(field)
                except:
                    print "E: [%s] %s: %s" % (filename, field_name, field)
                    pass

################################################################################

def check_build_depends():
    os.path.walk(Cnf["Dir::Root"], chk_bd_process_dir, None)

################################################################################

def main ():
    global Cnf, projectB, db_files, waste, excluded

    Cnf = daklib.utils.get_conf()
    Arguments = [('h',"help","Check-Archive::Options::Help")]
    for i in [ "help" ]:
	if not Cnf.has_key("Check-Archive::Options::%s" % (i)):
	    Cnf["Check-Archive::Options::%s" % (i)] = ""

    args = apt_pkg.ParseCommandLine(Cnf, Arguments, sys.argv)

    Options = Cnf.SubTree("Check-Archive::Options")
    if Options["Help"]:
	usage()

    if len(args) < 1:
        daklib.utils.warn("dak check-archive requires at least one argument")
        usage(1)
    elif len(args) > 1:
        daklib.utils.warn("dak check-archive accepts only one argument")
        usage(1)
    mode = args[0].lower()

    projectB = pg.connect(Cnf["DB::Name"], Cnf["DB::Host"], int(Cnf["DB::Port"]))
    daklib.database.init(Cnf, projectB)

    if mode == "md5sums":
        check_md5sums()
    elif mode == "files":
        check_files()
    elif mode == "dsc-syntax":
        check_dscs()
    elif mode == "missing-overrides":
        check_override()
    elif mode == "source-in-one-dir":
        check_source_in_one_dir()
    elif mode == "timestamps":
        check_timestamps()
    elif mode == "tar-gz-in-dsc":
        check_missing_tar_gz_in_dsc()
    elif mode == "validate-indices":
        check_indices_files_exist()
    elif mode == "files-not-symlinks":
        check_files_not_symlinks()
    elif mode == "validate-builddeps":
        check_build_depends()
    else:
        daklib.utils.warn("unknown mode '%s'" % (mode))
        usage(1)

################################################################################

if __name__ == '__main__':
    main()