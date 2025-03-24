#!/bin/bash

FILENAME=python_runner.deb

parent_folder=$(pwd)
echo $parent_folder
cd ..

dpkg-deb --build $parent_folder $parent_folder/build/$FILENAME
echo "build/python_runner.deb created"

sudo dpkg -i $parent_folder/build/$FILENAME
echo "python_runner installed"
