@echo off

rem Set the environment variable for CUDA
set "CUDA_VISIBLE_DEVICES=0"

rem Run your Python script with the specified arguments
python your_script.py -input_path input.txt -output_path output.txt -verbose_level 2

endlocal

