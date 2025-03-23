#!/bin/bash

FILENAME=python_runner.deb

parent_folder=$(pwd)
echo $parent_folder
cd ..
dpkg-deb --build $parent_folder $parent_folder/build/$FILENAME

sudo dpkg -i $parent_folder/build/$FILENAME