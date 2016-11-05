#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Based on:
#   https://github.com/samrushing/face_extractor
#   https://github.com/bdwilson/iPhotoDump
#   https://github.com/namezys/mac_photos

import sqlite3
import os
import sys
import shutil
import errno
import tempfile
import argparse
import signal
import filecmp

if sys.version[0] == '2':
    reload(sys)
    sys.setdefaultencoding('utf8')


# shows progress bar
def bar(progress):
    i = int(progress / 5)
    sys.stdout.write('\r')
    sys.stdout.write("[%-20s] %d%%" % ('=' * i, progress))
    sys.stdout.write('\r')
    sys.stdout.flush()


# closes database and removes temp files
def clean_up():
    main_db.close()
    shutil.rmtree(tempDir)
    print("\nDeleted temporary files")


# create dir if not exists
def make_sure_path_exists(path):
    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise


def signal_handler(signal, frame):
    clean_up()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)

# options
parser = argparse.ArgumentParser(description='Exports Photos Library to directory',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('-s', '--source', default="/Volumes/Transcend/Zdjęcia.photoslibrary",
                    help='source, path to Photos.app library')
parser.add_argument('-d', '--destination', default="/Volumes/photo", help='destination, path to external directory')
parser.add_argument('-c', '--compare', default=False, help='compare files', action="store_true")
parser.add_argument('-n', '--dryrun', default=False, help='do not copy files', action="store_true")
parser.add_argument('-m', '--masters', default=False, help='export masters instead of edited', action="store_true")
parser.add_argument('-a', '--album', default=None, help='export only a single album (debug)')
group1 = parser.add_mutually_exclusive_group()
group1.add_argument('-l', '--links', default=False, help='use symlinks', action="store_true")
group1.add_argument('-i', '--hardlinks', default=False, help='use hardlinks', action="store_true")
group2 = parser.add_mutually_exclusive_group()
group2.add_argument('-p', '--progress', help="show progress bar", default=True, action="store_true")
group2.add_argument('-v', '--verbose', help="increase output verbosity", action="store_true")
args = parser.parse_args()

if args.verbose:
    args.progress = False
if args.progress:
    args.verbose = False

libraryRoot = args.source
destinationRoot = args.destination

if not os.path.isdir(destinationRoot):
    sys.stderr.write('destination is not a directory?\n')
    sys.exit(-1)

# copy database, we don't want to mess with original
tempDir = tempfile.mkdtemp()
databasePathLibrary = os.path.join(tempDir, 'Library.apdb')
databasePathEdited = os.path.join(tempDir, 'ImageProxies.apdb')
shutil.copyfile(os.path.join(libraryRoot, 'Database/Library.apdb'), databasePathLibrary)
shutil.copyfile(os.path.join(libraryRoot, 'Database/ImageProxies.apdb'), databasePathEdited)

# connect to database
main_db = sqlite3.connect(databasePathLibrary)
main_db.execute("attach database ? as L", (databasePathLibrary,))
proxies_db = sqlite3.connect(databasePathEdited)
proxies_db.execute("attach database ? as L", (databasePathEdited,))

# cannot use one connection to do everything
connectionLibrary = main_db.cursor()
connectionEdited = proxies_db.cursor()

images = 0

# count all images
all_images_album_query = "select RKAlbum.modelid from L.RKAlbum where RKAlbum.albumSubclass=3" \
                         " and (RKAlbum.name <> 'printAlbum' and RKAlbum.name <> 'Last Import')"
if args.album is not None:
    if args.verbose:
        print("Processing album '{0}' only".format(args.album))
        all_images_album_query += " and RKAlbum.name = '" + args.album + "'"
for row in connectionLibrary.execute(all_images_album_query):
    albumNumber = (row[0],)
    connection2 = main_db.cursor()
    # get all photos in that album
    for row2 in connection2.execute("select RKAlbumVersion.VersionId from L.RKAlbumVersion "
                                    "where RKAlbumVersion.albumId = ?", albumNumber):
        versionId = (row2[0],)
        images += 1

print("Found " + str(images) + " images")

copied = 0
progress = 0
failed = 0

# find all "normal" albums
connectionLibrary = main_db.cursor()
album_query = "select RKAlbum.modelid, RKAlbum.name from L.RKAlbum where RKAlbum.albumSubclass=3" \
              " and (RKAlbum.name <> 'printAlbum' and RKAlbum.name <> 'Last Import') "
if args.album is not None:
    album_query += " and RKAlbum.name = '" + args.album + "'"
for row in connectionLibrary.execute(album_query):
    albumNumber = (row[0],)
    albumName = row[1]
    destinationDirectory = os.path.join(destinationRoot, albumName)
    make_sure_path_exists(destinationDirectory)
    if args.verbose:
        print(albumName + ":")
    connection2 = main_db.cursor()
    # get all photos in that album
    for row2 in connection2.execute(
            "select RKAlbumVersion.VersionId from L.RKAlbumVersion where RKAlbumVersion.albumId = ?", albumNumber):
        versionId = (row2[0],)
        connection3 = main_db.cursor()
        # get image path/name
        for row in connection3.execute(
                "select M.imagePath, V.fileName, V.adjustmentUUID from L.RKVersion as V inner join L.RKMaster as M on "
                "V.masterUuid=M.uuid where V.modelId = ?",
                versionId):
            progress += 1
            if args.progress:
                bar(progress * 100 / images)
            imagePath = row[0]
            fileName = row[1]
            adjustmentUUID = row[2]
            sourceImage = os.path.join(libraryRoot, "Masters", imagePath)
            # copy edited image to destination
            if not args.masters:
                if adjustmentUUID != "UNADJUSTEDNONRAW" and adjustmentUUID != "UNADJUSTED":
                    try:
                        connectionEdited.execute("SELECT resourceUuid, filename FROM RKModelResource "
                                                 "WHERE resourceTag=?", [adjustmentUUID])
                        uuid, fileName = connectionEdited.fetchone()
                        p1 = str(ord(uuid[0]))
                        p2 = str(ord(uuid[1]))
                        sourceImage = os.path.join(libraryRoot, "resources/modelresources", p1, p2, uuid, fileName)
                    except:
                        print("Fail to get edited version of source image, reverting to master version ({0})"
                              .format(adjustmentUUID))
                        print("Offending file is {0}, {1} with destination {2}".format(imagePath, fileName, albumName))
                        # sourceImage remains the same
            destinationPath = os.path.join(destinationDirectory, fileName)
            if args.verbose:
                print("\t(" + str(progress) + "/" + str(images) + ") From:\t" + sourceImage
                      + "\tto:\t" + destinationPath)
            if not os.path.isfile(destinationPath):
                copied += 1
                if args.verbose:
                    print("Copying")
                if not args.dryrun:
                    try:
                        if args.links:
                            os.symlink(sourceImage, os.path.join(destinationDirectory, os.path.basename(sourceImage)))
                        elif args.hardlinks:
                            os.link(sourceImage, os.path.join(destinationDirectory, os.path.basename(sourceImage)))
                        else:
                            shutil.copy(sourceImage, destinationDirectory)
                    except IOError:
                        failed += 1
                        print("Failed to copy: %s. Skipping this element." % sourceImage)
            else:
                if args.verbose:
                    print("File already exists")
                    if args.compare:
                        if args.verbose:
                            print("Comparing files...")
                        if not filecmp.cmp(sourceImage, destinationPath):
                            copied += 1
                            if not args.dryrun:
                                if args.verbose:
                                    print("Copying")
                                try:
                                    if args.links:
                                        os.symlink(sourceImage,
                                                   os.path.join(destinationDirectory, os.path.basename(sourceImage)))
                                    elif args.hardlinks:
                                        os.link(sourceImage,
                                                os.path.join(destinationDirectory, os.path.basename(sourceImage)))
                                    else:
                                        shutil.copy(sourceImage, destinationDirectory)
                                except IOError:
                                    failed += 1
                                    print("Failed to copy: %s. Skipping this element." % sourceImage)
                        else:
                            if args.verbose:
                                print("{0} and {1} are identical files. Ignoring.".format(sourceImage, destinationPath))

print("\nImages:\t" + str(images) + "\tcopied:\t" + str(copied) + "\tfailed:\t" + str(failed))

clean_up()
