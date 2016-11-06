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
import fnmatch

from os.path import basename

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


# copy as user wants
def effective_copy(links, hardlinks, src_img, dest_dir, dest_name=None):
    if dest_name is None:
        dest_name = os.path.basename(src_img)
    if links:
        os.symlink(src_img, os.path.join(dest_dir, dest_name))
    elif hardlinks:
        os.link(src_img, os.path.join(dest_dir, dest_name))
    else:
        shutil.copy(src_img, os.path.join(destinationDirectory, dest_name))


# find files in filesystem
def find(pattern, path):
    result = []
    for root, dirs, files in os.walk(path):
        for name in files:
            if fnmatch.fnmatch(name, pattern):
                result.append(os.path.join(root, name))
    return result


# logic to find files (edited and live photos) with modelId. It's heuristic based and must work in almost cases.
def get_resource_location(param):
    model_id_hex = hex(param)
    res_file_code = model_id_hex[2:]  # file code has not leading zeros, it's like a number
    # folder name has another logic...
    model_id_hex = model_id_hex[2:]  # remove "0x"
    # if lenght of hex code is not 4, compose with leading zeros
    if len(model_id_hex) < 4:
        zeros_needed = 4 - len(model_id_hex)
        zeros = "0" * zeros_needed
        model_id_hex = zeros + model_id_hex
    res_folder_name = model_id_hex[0:2]  # folder name mark
    return res_file_code, res_folder_name


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
parser.add_argument('-a', '--album', default=None, help='export album starting with... (for debug)')
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

# copy databases, we don't want to mess with original
tempDir = tempfile.mkdtemp()

# Handle photos 2.0 (Macos 10.12) new path
databasePathLibrary = os.path.join(tempDir, 'photos.db')
shutil.copyfile(os.path.join(libraryRoot, 'Database/photos.db'), databasePathLibrary)
# connect to database - 10.12 has only one database file
main_db = sqlite3.connect(databasePathLibrary)
main_db.execute("attach database ? as L", (databasePathLibrary,))

# can use one connection to do everything
connectionLibrary = main_db.cursor()

images = 0

# count all images
all_images_album_query = "select RKAlbum.modelid from L.RKAlbum where RKAlbum.albumSubclass=3" \
                         " and (RKAlbum.name <> 'printAlbum' and RKAlbum.name <> 'Last Import')"
if args.album is not None:
    all_images_album_query += " and RKAlbum.name like '" + args.album + "%'"
    if args.verbose:
        print("Processing album '{0}' only".format(args.album))
for row_album_count in connectionLibrary.execute(all_images_album_query):
    albumNumber = (row_album_count[0],)
    connection2 = main_db.cursor()
    # get all valid photos in that album
    valid_versions_query = "SELECT AV.VersionId " \
                           "FROM RKAlbumVersion as AV inner join RKVersion as V on AV.versionId = V.modelId " \
                           "                          inner join RKMaster as M on V.masterUuid=M.uuid " \
                           "WHERE (M.isMissing = 0) and (M.isInTrash = 0) and (V.isInTrash = 0) " \
                           "  and (V.showInLibrary = 1) and AV.albumId = ?"
    for row_album_version_count in connection2.execute(valid_versions_query, albumNumber):
        versionId = (row_album_version_count[0],)
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
    album_query += " and RKAlbum.name like '" + args.album + "%'"
for row_album in connectionLibrary.execute(album_query):
    albumNumber = (row_album[0],)
    albumName = row_album[1]
    destinationDirectory = os.path.join(destinationRoot, albumName)
    make_sure_path_exists(destinationDirectory)
    if args.verbose:
        print(albumName + ":")
    connection_album = main_db.cursor()
    # get all photos in that album
    for row_album_version in connection_album.execute(
            "select RKAlbumVersion.VersionId from L.RKAlbumVersion where RKAlbumVersion.albumId = ?", albumNumber):
        versionId = (row_album_version[0],)
        connection_version = main_db.cursor()
        # get image path/name
        for row_photo in connection_version.execute(
                "SELECT M.imagePath, V.fileName, V.adjustmentUUID, V.specialType, M.modelId FROM L.RKVersion as V "
                "inner join L.RKMaster as M on V.masterUuid=M.uuid WHERE (M.isMissing = 0) and (M.isInTrash = 0) and "
                "(V.isInTrash = 0) and (V.showInLibrary = 1) and (V.modelId = ?)", versionId):
            progress += 1
            if args.progress:
                bar(progress * 100 / images)
            imagePath = row_photo[0]
            fileName = row_photo[1]
            adjustmentUUID = row_photo[2]
            specialType = row_photo[3]  # looks like a live photo mark (values 5 - normal or 8 - hdr)
            master_model_id = row_photo[4]
            # To handle live photos, source image now is a vector with 1 or 2 values
            # 0 will always be the JPG file
            # 1 will be the MOV file, in case of live photo
            # Every position of the vector will be a tuple with "original path" and "destination file name"
            sourceImage = []
            sourceImage.append((os.path.join(libraryRoot, "Masters", imagePath), fileName))  # [0]
            # copy edited image to destination
            if not args.masters:
                if adjustmentUUID != "UNADJUSTEDNONRAW" and adjustmentUUID != "UNADJUSTED":
                    try:
                        connection_edited = main_db.cursor()
                        connection_edited.execute("SELECT modelId FROM RKModelResource WHERE resourceTag = '{0}' "
                                                  "and UTI = 'public.jpeg'".format(adjustmentUUID))
                        file_code, folder_name = get_resource_location(connection_edited.fetchone()[0])
                        edited_photos_start_path = os.path.join(libraryRoot, "resources", "media", "version",
                                                                folder_name)
                        edited_photos = find("*_{0}.jpeg".format(file_code), edited_photos_start_path)
                        sourceImage[0] = (edited_photos[0], fileName)  # [0]
                    except:
                        print("Fail to get edited version of source image, reverting to master version ({0})"
                              .format(adjustmentUUID))
                        print("Offending file is {0}, {1} with destination {2}".format(imagePath, fileName, albumName))
                        # sourceImage[0] remains the same
                # Handle live photos - start
                if specialType == 5 or specialType == 8:
                    try:
                        if args.verbose:
                            print(fileName + " seems to be a live photo, with specialType = " + str(specialType))
                        connection_live = main_db.cursor()
                        connection_live.execute("SELECT modelId FROM RKModelResource WHERE attachedModelId = {0} "
                                                  "and UTI = 'com.apple.quicktime-movie'".format(int(master_model_id)))
                        file_code, folder_name = get_resource_location(connection_live.fetchone()[0])
                        live_photos_start_path = os.path.join(libraryRoot, "resources", "media", "master", folder_name)
                        live_photos_movies = find("jpegvideocomplement_{0}.mov".format(file_code),
                                                  live_photos_start_path)
                        sourceImage.append((live_photos_movies[0], fileName+".MOV"))  # [1]
                    except:
                        print("Fail to get video from live photo ({0})".format(fileName))
                        print("Offending file is {0}, {1} with destination {2}".format(imagePath, fileName, albumName))
                        # sourceImage[1] will not exist in array
                # Handle live photos - end
            #
            for src_img_copy_vector in sourceImage:
                src_img_copy = src_img_copy_vector[0]
                dest_file_name = src_img_copy_vector[1]
                destinationPath = os.path.join(destinationDirectory, dest_file_name)
                if args.verbose:
                    print("\t(" + str(progress) + "/" + str(images) + ") From:\t" + src_img_copy
                          + "\tto:\t" + destinationPath)
                if not os.path.isfile(destinationPath):
                    copied += 1
                    if args.verbose:
                        print("Copying")
                    if not args.dryrun:
                        try:
                            effective_copy(args.links, args.hardlinks, src_img_copy, destinationDirectory, dest_file_name)
                        except IOError:
                            failed += 1
                            print("Failed to copy: %s. Skipping this element." % src_img_copy)
                else:
                    if args.verbose:
                        print("File already exists")
                        if args.compare:
                            if args.verbose:
                                print("Comparing files...")
                            if not filecmp.cmp(src_img_copy, destinationPath):
                                copied += 1
                                if not args.dryrun:
                                    if args.verbose:
                                        print("Copying")
                                    try:
                                        effective_copy(args.links, args.hardlinks, src_img_copy, destinationDirectory, dest_file_name)
                                    except IOError:
                                        failed += 1
                                        print("Failed to copy: %s. Skipping this element." % src_img_copy)
                            else:
                                if args.verbose:
                                    print("{0} and {1} are identical. Ignoring.".format(src_img_copy, destinationPath))

print("\nImages:\t" + str(images) + "\tcopied:\t" + str(copied) + "\tfailed:\t" + str(failed))

clean_up()
