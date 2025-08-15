# External libraries import statements
import sys
import json
import pathlib
import os.path
import logging
import unittest
import collections


# This is necessary in order for the tests to recognize local utilities
testdir = os.path.dirname(__file__)
srcdir = "../../../../../../stacks/collector/src/python"
absolute = os.path.abspath(os.path.join(testdir, srcdir))
sys.path.insert(0, absolute)

# This application's import statements
import utils.hPatrolUtils as hput


class TestHPatrolUtils(unittest.TestCase):
    # Will use logger to verify output from XXXXXX
    logger = logging.getLogger(__name__)
    logging.basicConfig(format = "%(asctime)s %(module)s %(levelname)s: %(message)s",
                    datefmt = "%m/%d/%Y %I:%M:%S %p", level = logging.DEBUG)


    def getAimpointDict(self, fileName):
        currentPath = pathlib.Path(__file__).parent.resolve()
        with open(f"{currentPath}/resource/{fileName}") as jsonFile:
            aimpoint = json.load(jsonFile)
        # self.logger.debug(aimpoint)
        return aimpoint


    def test_FFMPEGBuilder_output(self):
        self.logger.info("Testing with transcodeOptions output set")
        aimpoint = self.getAimpointDict("aimpoint-stream-output.json")
        transcodeOptions = aimpoint["transcodeOptions"]
        self.logger.info(transcodeOptions)
        inputSource = "inputSource"
        outputSource = "outputSource"
        ffmpegCommand = hput.FFMPEGBuilder(inputSource, outputSource, transcodeOptions).renderCommand()
        self.assertTrue(inputSource in ffmpegCommand)
        self.assertTrue(outputSource in ffmpegCommand)
        for key, value in transcodeOptions["output"].items():
            self.assertTrue(key in ffmpegCommand)
            if value != None:
                self.assertTrue(value in ffmpegCommand)
        self.logger.info(ffmpegCommand)


    def test_FFMPEGBuilder_input(self):
        self.logger.info("Testing with transcodeOptions input set")
        aimpoint = self.getAimpointDict("aimpoint-stream-input.json")
        transcodeOptions = aimpoint["transcodeOptions"]
        self.logger.info(aimpoint["transcodeOptions"])
        inputSource = "inputSource"
        outputSource = "outputSource"
        ffmpegCommand = hput.FFMPEGBuilder(inputSource, outputSource, transcodeOptions).renderCommand()
        self.assertTrue(inputSource in ffmpegCommand)
        self.assertTrue(outputSource in ffmpegCommand)
        for key, value in transcodeOptions["input"].items():
            self.assertTrue(key in ffmpegCommand)
            if value != "":
                self.assertTrue(value in ffmpegCommand)
            else:
                self.assertFalse(value in ffmpegCommand)
        self.logger.info(ffmpegCommand)


    def test_FFMPEGBuilder_input_output(self):
        self.logger.info("Testing with transcodeOptions input and output set")
        aimpoint = self.getAimpointDict("aimpoint-stream-input-output.json")
        transcodeOptions = aimpoint["transcodeOptions"]
        self.logger.info(aimpoint["transcodeOptions"])
        inputSource = "inputSource"
        outputSource = "outputSource"
        ffmpegCommand = hput.FFMPEGBuilder(inputSource, outputSource, transcodeOptions).renderCommand()
        self.assertTrue(inputSource in ffmpegCommand)
        self.assertTrue(outputSource in ffmpegCommand)
        for key, value in transcodeOptions["input"].items():
            self.assertTrue(key in ffmpegCommand)
            if value != "":
                self.assertTrue(value in ffmpegCommand)
            else:
                self.assertFalse(value in ffmpegCommand)
        for key, value in transcodeOptions["output"].items():
            self.assertTrue(key in ffmpegCommand)
            if value != "":
                self.assertTrue(value in ffmpegCommand)
            else:
                self.assertFalse(value in ffmpegCommand) 
        self.logger.info(ffmpegCommand)


    def test_FFMPEGBuilder_no_ffmpg(self):
        self.logger.info("Testing with no input and output set")
        inputSource = "inputSource"
        outputSource = "outputSource"
        ffmpegCommand = hput.FFMPEGBuilder(inputSource, outputSource, None).renderCommand()
        self.assertTrue(inputSource in ffmpegCommand)
        self.assertTrue(outputSource in ffmpegCommand)
        self.logger.info(ffmpegCommand)


    def test_hashCommand(self):
        self.logger.info("Testing hash command")
        testFile = "/tmp/test.txt"
        command = f"ffmpeg -hide_banner -i {testFile} -map 0:v -f md5 - ".split()
        ffmpegCommand = hput.FFMPEGBuilder(testFile, "-")
        ffmpegCommand.input({"-hide_banner":""})
        ffmpegCommand.output({
            "-map":"0:v",
            "-f":"md5"
            })
        ffmpegCommandOutput = ffmpegCommand.renderCommand()
        self.logger.info(f"Expected => {command}")
        self.logger.info(f"Actual   => {ffmpegCommandOutput}")

        self.assertEqual(command, ffmpegCommandOutput)


    def test_overrideCommand(self):
        self.logger.info("Testing preserving initial configuration")
        transcodeOptions = {
                "input": {
                    "-hide_banner": "",
                    "-f": "concat" 
                },
                "output": {
                    "-acodec": "copy",
                    "-vcodec": "copy",
                    "-v": "error"
                }
            }
        inputSource = "inputSource"
        outputSource = "outputSource"
        ffmpegObj = hput.FFMPEGBuilder(inputSource, outputSource, transcodeOptions)
        ffmpegObj.ffmpeg = "ffmpeg"
        ffmpegCommand = ffmpegObj.renderCommand()
        actualCommandString = " ".join(ffmpegCommand)
        expectedCommandString = f"ffmpeg -hide_banner -f concat -i {inputSource} -acodec copy -vcodec copy -v error {outputSource}"
        self.assertEqual(expectedCommandString, actualCommandString)
        # try to change defaulst
        ffmpegObj.input({"-f": "noise"})
        ffmpegCommand = ffmpegObj.renderCommand()
        # Test to ensure that transcodeOptions are preserved even if attempting to override with defaults
        self.assertFalse("noise" in ffmpegCommand)
        self.assertTrue(collections.Counter(ffmpegCommand) == collections.Counter(expectedCommandString.split()))
        ffmpegObj.output({"-map": "0:v"})
        ffmpegCommand = ffmpegObj.renderCommand()
        self.assertTrue("-map" in ffmpegCommand)
        self.assertTrue("0:v" in ffmpegCommand)


    def test_timeLapseCommand(self):
        self.logger.info("Testing timeLapse FFMPEG command while preserving initial configuration")
        framerate = "25"
        filePattern = "test"
        outFile = "outFile"
        expectedTimeLapseCommand = f"ffmpeg -hide_banner -y -framerate {framerate} -pattern_type glob -i {filePattern} -vcodec libx264 -crf 0 -v error {outFile}".split() 
        jsonOption = '{"transcodeOptions": {}}'
        transcodeOptions = json.loads(jsonOption)
        builder = hput.FFMPEGBuilder(filePattern, outFile, transcodeOptions["transcodeOptions"])
        builder.input(
            {
                "-hide_banner": "", 
                "-y": "",
                "-framerate": framerate,
                "-pattern_type": "glob" 
             })
        builder.output(
            {
                "-vcodec": "libx264", 
                "-crf": "0",
                "-v": "error" 
             })
        ffmpegCommandOutput = builder.renderCommand()
        self.assertEqual(expectedTimeLapseCommand, ffmpegCommandOutput)

        # Initialize class with input option changing the framerate from transcodeOptions
        jsonOption = '{"transcodeOptions": { "input": {"-framerate": "30" } } }'
        transcodeOptions = json.loads(jsonOption)
        builder = hput.FFMPEGBuilder(filePattern, outFile, transcodeOptions["transcodeOptions"])
        # Set defaults but ensure that framerate from options is not overriden
        builder.input(
            {
                "-hide_banner": "",
                "-y": "",
                "-framerate": framerate,
                "-pattern_type": "glob"
             })
        builder.output(
            {
                "-vcodec": "libx264",
                "-crf": "0",
                "-v": "error"
             })
        # Expteced with framerate 30
        expectedTimeLapseCommand = f"ffmpeg -hide_banner -y -framerate 30 -pattern_type glob -i {filePattern} -vcodec libx264 -crf 0 -v error {outFile}".split()
        ffmpegCommandOutput = builder.renderCommand()
        self.assertEqual(expectedTimeLapseCommand, ffmpegCommandOutput)
        self.logger.info(f"Expected => {expectedTimeLapseCommand}")
        self.logger.info(f"Actual   => {ffmpegCommandOutput}")


    def test_goodTranscodeCommand(self):
        self.logger.info("Testing goodTranscode FFMPEG command while preserving initial configuration")
        aTempFile = "inFile"
        outFile = "outFile"
        expectedGoodTranscodeCommand = f"ffmpeg -hide_banner -f concat -i {aTempFile} -acodec copy -vcodec copy -v error {outFile}".split()
        jsonOption = '{"transcodeOptions": {}}'
        transcodeOptions = json.loads(jsonOption)
        builder = hput.FFMPEGBuilder(aTempFile, outFile, transcodeOptions["transcodeOptions"])
        builder.input(
            {
                "-hide_banner": "",
                "-f": "concat"
             })
        builder.output(
            {
                "-acodec": "copy",
                "-vcodec": "copy",
                "-v": "error" 
             })
        ffmpegCommandOutput = builder.renderCommand()
        self.assertEqual(expectedGoodTranscodeCommand, ffmpegCommandOutput)

        # commented out 09/13/22: this call would reduce the bitrate
        # subprocess.run(f"{config['ffmpeg']} -f concat -i {aTempFile} -c:v libx264 -c:a aac -b:v 97k {outFile} -v error".split())

        expectedGoodTranscodeCommand = f"ffmpeg -f concat -i {aTempFile} -c:v libx264 -c:a aac -b:v 97k {outFile} -v error".split()

        jsonOption = ('{"transcodeOptions":'
                        '{ "input": '
                            '{'
                                '"-hide_banner":null'
                            '},'
                            '"output": '
                            '{'
                                '"-c:v": "libx264", '
                                '"-c:a": "aac",'
                                '"-b:v": "97k",'
                                '"-acodec": null,'
                                '"-vcodec": null'
                            '}'
                        '}'
                      '}')
        transcodeOptions = json.loads(jsonOption)
        builder = hput.FFMPEGBuilder(aTempFile, outFile, transcodeOptions["transcodeOptions"])
        builder.input(
            {
                "-hide_banner": "",
                "-f": "concat"
             })
        builder.output(
            {
                "-acodec": "copy", 
                "-vcodec": "copy",
                "-v": "error" 
             })
        ffmpegCommandOutput = builder.renderCommand()
        self.logger.info(f"Expected => {expectedGoodTranscodeCommand}")
        self.logger.info(f"Actual   => {ffmpegCommandOutput}")
        self.assertTrue(collections.Counter(expectedGoodTranscodeCommand) == collections.Counter(ffmpegCommandOutput))
