#!/bin/sh

# Script to establish the softlinks required for CLI execution
# No need to run it if CLI execution is not needed
# Meant to be ran from the project's root directory

components="collector disabler dispatcher drover enabler historian marshal minion monitor scheduler transcoder"

generators="astrakhanParser avantaParser bazaNetParser cam72Parser ceriumParser cud59Parser fluorineParser hangEmHighParser interraParser iodineParser ipcamRdtcParser itsvideoParser lantaMeParser norwayParser moidomParser saferegionNetParser theGauntletParser thoriumParser trafficlandParser ufanetParser xenonParser"


linkComponents()
{
    echo "Linking core components"
    for aComponent in $components
    do
        echo "      $aComponent"
        ln -s ../../../common/src/python/utils stacks/$aComponent/src/python/
        ln -s ../../../common/src/python/orangeUtils stacks/$aComponent/src/python/
        ln -s ../../../common/src/python/systemMode.py stacks/$aComponent/src/python/
        ln -s ../../../common/src/python/exceptions.py stacks/$aComponent/src/python/
        ln -s ../../../common/src/python/processInit.py stacks/$aComponent/src/python/
        ln -s ../../../common/src/python/superGlblVars.py stacks/$aComponent/src/python/
        ln -s ../../../common/src/python/systemSettings.py stacks/$aComponent/src/python/
        ln -s ../../../common/src/python/collectionTypes.py stacks/$aComponent/src/python/
    done
    echo "Linking testResources for some components only"
    ln -s ../common/testResources stacks/marshal/
    ln -s ../common/testResources stacks/collector/
    ln -s ../common/testResources stacks/scheduler/
    ln -s ../common/testResources stacks/historian/
    ln -s ../common/testResources stacks/dispatcher/

    echo ""
}

linkGenerators()
{
    echo "Linking aimpoint generators"
    for aComponent in $generators
    do
        echo "      $aComponent"
        ln -s ../../../../common/src/python/utils stacks/generators/$aComponent/src/python/
        ln -s ../../../../common/src/python/orangeUtils stacks/generators/$aComponent/src/python/
        ln -s ../../../../common/src/python/exceptions.py stacks/generators/$aComponent/src/python/
        ln -s ../../../../common/src/python/comparitor.py stacks/generators/$aComponent/src/python/
        ln -s ../../../../common/src/python/systemMode.py stacks/generators/$aComponent/src/python/
        ln -s ../../../../common/src/python/processInit.py stacks/generators/$aComponent/src/python/
        ln -s ../../../../common/src/python/superGlblVars.py stacks/generators/$aComponent/src/python/
        ln -s ../../../../common/src/python/systemSettings.py stacks/generators/$aComponent/src/python/
        ln -s ../../../../common/src/python/collectionTypes.py stacks/generators/$aComponent/src/python/
    done
}


ulinkGenerators()
{
    echo "Deleting generator specific links"
    for aGenerator in $generators
    do
        # Clean it up
        rm stacks/generators/$aGenerator/src/python/comparitor.py
    done
}


addGenerators()
{
    for aGenerator in $generators
    do
        # echo "Adding $aGenerator"
        components="$components generators/$aGenerator"
        # components="generators/$aGenerator $components"
    done
}

cleanAll()
{
    for aComponent in $components
    do
        # Clean it up
        echo "Deleting softlinks for $aComponent"
        rm stacks/$aComponent/src/python/utils
        rm stacks/$aComponent/src/python/orangeUtils
        rm stacks/$aComponent/src/python/exceptions.py
        rm stacks/$aComponent/src/python/systemMode.py
        rm stacks/$aComponent/src/python/processInit.py
        rm stacks/$aComponent/src/python/superGlblVars.py
        rm stacks/$aComponent/src/python/systemSettings.py
        rm stacks/$aComponent/src/python/collectionTypes.py
    done
    echo "Deleting testResources softlinks for the Collector, Historian, Dispatcher, and Marshal only"
    rm stacks/marshal/testResources
    rm stacks/collector/testResources
    rm stacks/scheduler/testResources
    rm stacks/historian/testResources
    rm stacks/dispatcher/testResources
 
    echo ""
}


case $1 in
    ln)
        linkComponents
        linkGenerators
        exit;;
    rm)
        ulinkGenerators
        addGenerators
        cleanAll
        exit;;
esac

echo "Invalid entry; options are 'ln' or 'rm'"
