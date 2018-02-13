# wdecoster
"""
Script for filtering and trimming of Oxford Nanopore technologies long reads.
Filtering can be done by calculating metrics while streaming,
or alternatively using a summary file as generated by albacore while basecalling.

Filtering can be done on length and average read basecall quality.
Trimming can be done from the beginning and the end of a read.

Reads from stdin, writes to stdout.

Intended to be used:
- directly after fastq extraction
- prior to mapping
- in a stream between extraction and mapping

Example usage:
gunzip -c reads.fastq.gz | \
 NanoFilt.py -q 10 -l 500 --headcrop 50 | \
 minimap2 genome.fa - | \
 samtools sort -@24 -o alignment.bam -
"""

from __future__ import print_function
from Bio import SeqIO
import sys
from argparse import ArgumentParser, ArgumentTypeError, HelpFormatter
from nanomath import ave_qual
from nanoget import process_summary
from nanofilt.version import __version__
import logging
import textwrap as _textwrap


class CustomHelpFormatter(HelpFormatter):
    def _fill_text(self, text, width, indent):
        return ''.join(indent + line for line in text.splitlines(keepends=True))

    def _split_lines(self, text, width):
        text = self._whitespace_matcher.sub(' ', text).strip()
        return _textwrap.wrap(text, 80)


def custom_formatter(prog):
    return CustomHelpFormatter(prog)


def default_formatter(prog):
    return HelpFormatter(prog)


def main():
    args = get_args(custom_formatter)
    try:
        logging.basicConfig(
            format='%(asctime)s %(message)s',
            filename="NanoFilt.log",
            level=logging.INFO)
    except PermissionError:
        pass  # indicates that user has no write permission in this directory. No logs then
    try:
        logging.info('NanoFilt {} started with arguments {}'.format(__version__, args))
        if args.tailcrop:
            args.tailcrop = -args.tailcrop
        if args.summary:
            filter_using_summary(sys.stdin, args)
        else:
            filter_stream(sys.stdin, args)
        logging.info('NanoFilt finished.')
    except Exception as e:
        logging.error(e, exc_info=True)
        raise


def get_args(custom_formatter):
    epilog = "EXAMPLES:\n" \
        "  gunzip -c reads.fastq.gz | NanoFilt -q 10 -l 500 --headcrop 50 | " \
        "minimap2 genome.fa - | samtools sort -O BAM -@24 -o alignment.bam -\n" \
        "  gunzip -c reads.fastq.gz | NanoFilt -q 12 --headcrop 75 | " \
        "gzip > trimmed-reads.fastq.gz\n" \
        "  gunzip -c reads.fastq.gz | NanoFilt -q 10 | gzip > highQuality-reads.fastq.gz\n"
    parser = ArgumentParser(
        description="Perform quality and/or length and/or GC filtering of Nanopore fastq data. \
          Reads on stdin.",
        epilog=epilog,
        formatter_class=custom_formatter,
        add_help=False)
    general = parser.add_argument_group(
        title='General options')
    general.add_argument("-h", "--help",
                         action="help",
                         help="show the help and exit")
    general.add_argument("-v", "--version",
                         help="Print version and exit.",
                         action="version",
                         version='NanoFilt {}'.format(__version__))
    filtering = parser.add_argument_group(
        title='Options for filtering reads on.')
    filtering.add_argument("-l", "--length",
                           help="Filter on a minimum read length",
                           default=1,
                           type=int)
    filtering.add_argument("-q", "--quality",
                           help="Filter on a minimum average read quality score",
                           default=0,
                           type=int)
    filtering.add_argument("--minGC",
                           help="Sequences must have GC content >= to this.  Float between 0.0 and 1.0. \
                              Ignored if using summary file.",
                           default=0.0,
                           type=valid_GC)
    filtering.add_argument("--maxGC",
                           help="Sequences must have GC content <= to this.  Float between 0.0 and 1.0. \
                              Ignored if using summary file.",
                           default=1.0,
                           type=valid_GC)
    trimming = parser.add_argument_group(
        title='Options for trimming reads.')
    trimming.add_argument("--headcrop",
                          help="Trim n nucleotides from start of read",
                          default=None,
                          type=int)
    trimming.add_argument("--tailcrop",
                          help="Trim n nucleotides from end of read",
                          default=None,
                          type=int)
    inputoptions = parser.add_argument_group(
        title='Input options.')
    inputoptions.add_argument("-s", "--summary",
                              help="Use summary file for quality scores")
    inputoptions.add_argument("--readtype",
                              help="Which read type to extract information about from summary. \
                              Options are 1D, 2D or 1D2",
                              default="1D",
                              choices=['1D', '2D', "1D2"])
    args = parser.parse_args()
    if args.minGC > args.maxGC:
        sys.exit("NanoFilt: error: argument --minGC should be smaller than --maxGC")
    if args.minGC == 0.0 and args.maxGC == 1.0:
        args.GC_filter = False
    else:
        args.GC_filter = True
    return args


def valid_GC(x):
    """type function for argparse to check GC values.

    Check if the supplied value for minGC and maxGC is a valid input, being between 0 and 1
    """
    x = float(x)
    if x < 0.0 or x > 1.0:
        raise ArgumentTypeError("{} not in range [0.0, 1.0]".format(x))
    return x


def length_record(rec, minlen):
    return len(rec) > minlen


def silent_quality_check(x):
    """When no quality check needs to be performed, simply return True"""
    return True


def filter_stream(fq, args):
    """Filter a fastq file on stdin.

    Print fastq record to stdout if it passes
    - quality filter (optional)
    - length filter (optional)
    - min/maxGC filter (optional)
    Optionally trim a number of nucleotides from beginning and end.
    Record has to be longer than args.length (default 1) after trimming
    Use a faster silent quality_check if no filtering on quality is required
    """
    sys.stderr.write(str(args.quality))
    if args.quality:
        quality_check = ave_qual
    else:
        quality_check = silent_quality_check
    minlen = args.length + int(args.headcrop or 0) - (int(args.tailcrop or 0))
    for rec in SeqIO.parse(fq, "fastq"):
        if args.GC_filter:
            gc = (rec.seq.upper().count("C") + rec.seq.upper().count("G")) / len(rec)
        else:
            gc = 0.50  # dummy variable
        if quality_check(rec.letter_annotations["phred_quality"]) > args.quality \
                and len(rec) > minlen \
                and args.minGC <= gc <= args.maxGC:
            print(rec[args.headcrop:args.tailcrop].format("fastq"), end="")


def filter_using_summary(fq, args):
    """Use quality scores from albacore summary file for filtering

    Use the summary file from albacore for more accurate quality estimate
    Get the dataframe from nanoget, convert to dictionary
    """
    data = {entry[0]: entry[1] for entry in process_summary(
        summaryfile=args.summary,
        threads="NA",
        readtype=args.readtype,
        barcoded=False)[
        ["readIDs", "quals"]].itertuples(index=False)}
    try:
        for record in SeqIO.parse(fq, "fastq"):
            if data[record.id] > args.quality and len(record) > args.length:
                print(record[args.headcrop:args.tailcrop].format("fastq"), end="")
    except KeyError:
        logging.error("mismatch between summary and fastq: \
                       {} was not found in the summary file.".format(record.id))
        sys.exit('\nERROR: mismatch between sequencing_summary and fastq file: \
                 {} was not found in the summary file.\nQuitting.'.format(record.id))


if __name__ == "__main__":
    main()
