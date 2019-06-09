import argparse
import json
import sys
import time

import tensorflow as tf
from pathlib import Path

from inputs import *
from model_fns import *
from predict_fns import *

# This program was designed to function with multiple kinds of models, but currently only GPT2 is supported
# The first element in the tupel is the model function, the second is the function called when predicting
models = {
    "GPT2": (gpt2_model, gpt2_predict)
}

inputs = {
    "openwebtext": openwebtext,  # Standard OpenWebtext input
    "openwebtext_longbiased": openwebtext_longbiased,
    # OpenWebtext with a bias towards showing more long (>512 tokens) examples
    "openwebtext_long": openwebtext_long,  # Openwebtext that only shows long examples
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=str)  # JSON file that contains model parameters
    parser.add_argument("--predict_file", type=str)  # File to take as input for predict
    parser.add_argument("--predict_text", type=str)  # Take string directly from args
    parser.add_argument("--top_k", type=int)  # Top K truncation parameter for text generation
    args = parser.parse_args()

    # Get prediction text
    predict_mode = False
    if args.predict_file is not None:
        predict_mode = True
        with open(args.predict_file) as f:
            text = f.read()
    elif args.predict_text is not None:
        predict_mode = True
        text = args.predict_text
    elif args.predict_file is not None and args.predict_text is not None:
        print("ERROR: Specify exactly one of --predict_file and --predict_text!")
        sys.exit()

    # Setup logging
    Path("logs").mkdir(exist_ok=True)
    tf.logging.set_verbosity(logging.INFO)
    handlers = [
        logging.FileHandler('logs/{}.log'.format(args.model)),
        logging.StreamHandler(sys.stdout)
    ]
    logger = logging.getLogger('tensorflow')
    logger.handlers = handlers

    # Read params of model
    with open(args.model, "r") as f:
        params = json.load(f)

    if args.top_k is not None:
        params["top_k"] = args.top_k

    if not "precision" in params.keys():
        params[
            "precision"] = "float32"  # Doesn't actually do anything since float32 is the default anyways. Only recognized other dtype is "bfloat16"

    if not "iterations" in params.keys():
        params["iterations"] = 1  # Because this controls how many samples are prefetched

    logger.info(params)

    model_fn = models[params["model"]][0]
    predict_fn = models[params["model"]][1]
    input_fn = inputs[params["input"]]

    # Non TPU setup
    if not predict_mode:
        params["batch_size"] = params["train_batch_size"]
    else:
        params["batch_size"] = params["predict_batch_size"]
    run_config = tf.estimator.RunConfig(
        model_dir=params["model_path"],
        session_config=tf.ConfigProto(
            # log_device_placement=True,
            # allow_soft_placement=True
        ),
    )

    network = tf.estimator.Estimator(
        model_fn=model_fn,
        config=run_config,
        params=params)

    if predict_mode:
        logger.info("Generating predictions...")
        predict_fn(network, text, params)
        sys.exit()

    # Train eval loop
    while True:
        start = time.time()

        network.train(
            input_fn=partial(input_fn, eval=False),
            steps=params["train_steps"])

        end = time.time()
        logger.info("\nTrain loop took {:.2f}s\n".format(end - start))

        eval_result = network.evaluate(
            input_fn=partial(input_fn, eval=True),
            steps=params["eval_steps"])

        logger.info("\nEval Results: {}\n".format(str(eval_result)))

        if network.get_variable_value("global_step") > params["max_steps"]:
            logger.info("Done!")
            break
