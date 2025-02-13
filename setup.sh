RD_PATH=`realpath $BASH_SOURCE | xargs dirname`
export PATH=$RD_PATH/scripts/performance:$PATH

echo "performance tools added to PATH"