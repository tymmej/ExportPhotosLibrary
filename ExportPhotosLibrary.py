#!/usr/bin/env python
# -*- Mode: Python -*-

#Based on:
#   https://github.com/samrushing/face_extractor
#   https://github.com/bdwilson/iPhotoDump


import sqlite3
import os
import sys
import time
import datetime
import shutil
import errno
import tempfile

reload(sys)
sys.setdefaultencoding('utf8')

def make_sure_path_exists(path):
    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

if len(sys.argv)<3:
    sys.stderr.write('Usage: %s <Root> <DestinationDirectory>\n' % (sys.argv[0],))
    sys.exit(-1)

lib_root=sys.argv[1]
dest=sys.argv[2]

if not os.path.isdir(dest):
    sys.stderr.write('destination is not a directory?\n')
    sys.exit(-1)

#copy database, we don't want to mess with original
tempDir=tempfile.mkdtemp()
databasePath=os.path.join(tempDir, 'Library.apdb')
databasePath2=(databasePath,)
shutil.copyfile(os.path.join(lib_root, 'Database/Library.apdb'), databasePath)
#connect to database
main_db=sqlite3.connect(databasePath)
main_db.execute("attach database ? as L", databasePath2)

#cannot use one connection to do everything
connection1=main_db.cursor()

images=0
copied=0

#find all "normal" albums
for row in connection1.execute("select RKAlbum.modelid, RKAlbum.name from L.RKAlbum where RKAlbum.albumSubclass=3"):
    albumNumber=(row[0],)
    albumName=row[1]
    print "-------"+albumName+"-------"
    connection2=main_db.cursor()
    #get all photos in that album
    for row2 in connection2.execute("select RKAlbumVersion.VersionId from L.RKAlbumVersion where RKAlbumVersion.albumId = ?", albumNumber):
        versionId=(row2[0],)
        connection3=main_db.cursor()
        #get image path/name
        for row in connection3.execute("select M.imagePath, V.fileName from L.RKVersion as V inner join L.RKMaster as M on V.masterUuid=M.uuid where V.modelId = ?", versionId):
            images+=1
            imagePath=row[0]
            fileName=row[1]
            source=os.path.join(lib_root, "Masters", imagePath)
            destination=os.path.join(dest, albumName, fileName)
            print "From:\t"+source+"\tto:\t"+destination
            make_sure_path_exists(destination)
            #Synology create folder for each file so we need to check against that folder instead of file.
            checkPath=os.path.join(dest, albumName, fileName, fileName)
            if not os.path.isfile(checkPath):
                copied+=1
                print "Copying"
                shutil.copy(source, destination)
            else:
                print "File already exists"

print "Images:\t"+str(images)+"\tcopied:\t"+str(copied)
#clean up
main_db.close()
shutil.rmtree(tempDir)
