from setuptools import setup, find_packages

setup(
    name="fins-py",
    version="0.1.0",
    author="IWIN-FINS Lab",
    author_email="stevenmhy@sjtu.edu.cn",
    description="Python SDK for FineVision orchestration",
    long_description=open("README.md").read() if hasattr(open("README.md"), "read") else "",
    long_description_content_type="text/markdown",
    url="https://github.com/Han-Yu-Meng/FineVision-Launch",
    packages=find_packages(),
    install_requires=[
        "requests",
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: POSIX :: Linux",
        "License :: OSI Approved :: MIT License",
    ],
    python_requires=">=3.8",
)