# -*- coding: utf-8 -*-
"""
# @Time    : Jun/17/2020
# @Author  : zhx
"""

import collections
import inspect
import hashlib
from datetime import datetime
from multiprocessing.pool import Pool
import numpy as np
import pandas as pd
import SimpleITK as sitk
from network_architecture.metrics import ConfusionMatrix, ALL_METRICS
from batchgenerators.utilities.file_and_folder_operations import *
from collections import OrderedDict


class Evaluator:
    """Object that holds prediction and reference segmentations with label information
    and computes a number of metrics on the two. 'labels' must either be an
    iterable of numeric values (or tuples thereof) or a dictionary with string
    names and numeric values.
    """
    default_metrics = [
        "False Positive Rate",
        "Dice",
        "Jaccard",
        "Precision",
        "Recall",
        "Accuracy",
        "False Omission Rate",
        "Negative Predictive Value",
        "False Negative Rate",
        "True Negative Rate",
        "False Discovery Rate",
        "Total Positives Test",
        "Total Positives Reference"
    ]

    default_advanced_metrics = [
        "Hausdorff Distance",
        "Hausdorff Distance 95",
        "Avg. Surface Distance",
        "Avg. Symmetric Surface Distance"
    ]

    def __init__(self,
                 prediction=None,
                 reference=None,
                 labels=None,
                 metrics=None,
                 advanced_metrics=None,
                 nan_for_nonexisting=True):

        self.prediction = None
        self.reference = None
        self.confusion_matrix = ConfusionMatrix()
        self.labels = None
        self.nan_for_nonexisting = nan_for_nonexisting
        self.result = None

        self.metrics = []
        if metrics is None:
            for m in self.default_metrics:
                self.metrics.append(m)
        else:
            for m in metrics:
                self.metrics.append(m)

        self.advanced_metrics = []
        if advanced_metrics is None:
            for m in self.default_advanced_metrics:
                self.advanced_metrics.append(m)
        else:
            for m in advanced_metrics:
                self.advanced_metrics.append(m)

        self.set_reference(reference)
        self.set_prediction(prediction)
        if labels is not None:
            self.set_labels(labels)
        else:
            if prediction is not None and reference is not None:
                self.construct_labels()

    def set_prediction(self, prediction):
        """Set the prediction segmentation."""
        self.prediction = prediction

    def set_reference(self, reference):
        """Set the reference segmentation."""
        self.reference = reference

    def set_labels(self, labels):
        """Set the labels.
        :param labels= may be a dictionary (int->str), a set (of ints), a tuple (of ints) or a list (of ints). Labels
        will only have names if you pass a dictionary"""

        if isinstance(labels, dict):
            self.labels = collections.OrderedDict(labels)
        elif isinstance(labels, set):
            self.labels = list(labels)
        elif isinstance(labels, np.ndarray):
            self.labels = [i for i in labels]
        elif isinstance(labels, (list, tuple)):
            self.labels = labels
        else:
            raise TypeError("Can only handle dict, list, tuple, set & numpy array, but input is of type {}".format(type(labels)))

    def construct_labels(self):
        """Construct label set from unique entries in segmentations."""

        if self.prediction is None and self.reference is None:
            raise ValueError("No prediction or reference segmentations.")
        elif self.prediction is None:
            labels = np.unique(self.reference)
        else:
            labels = np.union1d(np.unique(self.prediction),
                                np.unique(self.reference))
        self.labels = list(map(lambda x: int(x), labels))

    def set_metrics(self, metrics):
        """Set evaluation metrics"""

        if isinstance(metrics, set):
            self.metrics = list(metrics)
        elif isinstance(metrics, (list, tuple, np.ndarray)):
            self.metrics = metrics
        else:
            raise TypeError("Can only handle list, tuple, set & numpy array, but input is of type {}".format(type(metrics)))

    def add_metric(self, metric):

        if metric not in self.metrics:
            self.metrics.append(metric)

    def evaluate(self, prediction=None, reference=None, advanced=True, **metric_kwargs):
        """Compute metrics for segmentations."""
        if prediction is not None:
            self.set_prediction(prediction)

        if reference is not None:
            self.set_reference(reference)

        if self.prediction is None or self.reference is None:
            raise ValueError("Need both prediction and reference segmentations.")

        if self.labels is None:
            self.construct_labels()

        self.metrics.sort()

        # get functions for evaluation
        # somewhat convoluted, but allows users to define additonal metrics
        # on the fly, e.g. inside an IPython console
        _funcs = {m: ALL_METRICS[m] for m in self.metrics + self.advanced_metrics}
        frames = inspect.getouterframes(inspect.currentframe())
        for metric in self.metrics:
            for f in frames:
                if metric in f[0].f_locals:
                    _funcs[metric] = f[0].f_locals[metric]
                    break
            else:
                if metric in _funcs:
                    continue
                else:
                    raise NotImplementedError(
                        "Metric {} not implemented.".format(metric))

        # get results
        self.result = OrderedDict()

        eval_metrics = self.metrics
        if advanced:
            eval_metrics += self.advanced_metrics

        if isinstance(self.labels, dict):

            for label, name in self.labels.items():
                k = str(name)
                self.result[k] = OrderedDict()
                if not hasattr(label, "__iter__"):
                    self.confusion_matrix.set_prediction(self.prediction == label)
                    self.confusion_matrix.set_reference(self.reference == label)
                else:
                    current_prediction = 0
                    current_reference = 0
                    for l in label:
                        current_prediction += (self.prediction == l)
                        current_reference += (self.reference == l)
                    self.confusion_matrix.set_prediction(current_prediction)
                    self.confusion_matrix.set_reference(current_reference)
                for metric in eval_metrics:
                    self.result[k][metric] = _funcs[metric](confusion_matrix=self.confusion_matrix,
                                                               nan_for_nonexisting=self.nan_for_nonexisting,
                                                               **metric_kwargs)

        else:
            for i, l in enumerate(self.labels):
                k = str(l)
                self.result[k] = OrderedDict()
                self.confusion_matrix.set_prediction(self.prediction == l)
                self.confusion_matrix.set_reference(self.reference == l)
                for metric in eval_metrics:
                    self.result[k][metric] = _funcs[metric](confusion_matrix=self.confusion_matrix,
                                                            nan_for_nonexisting=self.nan_for_nonexisting,
                                                            **metric_kwargs)

        return self.result

    def to_dict(self):

        if self.result is None:
            self.evaluate()
        return self.result

    def to_array(self):
        """Return result as numpy array (labels x metrics)."""

        if self.result is None:
            self.evaluate()

        result_metrics = sorted(self.result[list(self.result.keys())[0]].keys())

        a = np.zeros((len(self.labels), len(result_metrics)), dtype=np.float32)

        if isinstance(self.labels, dict):
            for i, label in enumerate(self.labels.keys()):
                for j, metric in enumerate(result_metrics):
                    a[i][j] = self.result[self.labels[label]][metric]
        else:
            for i, label in enumerate(self.labels):
                for j, metric in enumerate(result_metrics):
                    a[i][j] = self.result[label][metric]

        return a

    def to_pandas(self):
        """Return result as pandas DataFrame."""

        a = self.to_array()

        if isinstance(self.labels, dict):
            labels = list(self.labels.values())
        else:
            labels = self.labels

        result_metrics = sorted(self.result[list(self.result.keys())[0]].keys())

        return pd.DataFrame(a, index=labels, columns=result_metrics)


class NiftiEvaluator(Evaluator):
    def __init__(self, *args, **kwargs):

        self.prediction_nifti = None
        self.reference_nifti = None
        super(NiftiEvaluator, self).__init__(*args, **kwargs)

    def set_prediction(self, prediction):
        """Set the prediction segmentation."""

        if prediction is not None:
            self.prediction_nifti = sitk.ReadImage(prediction)
            super(NiftiEvaluator, self).set_prediction(sitk.GetArrayFromImage(self.prediction_nifti))
        else:
            self.prediction_nifti = None
            super(NiftiEvaluator, self).set_prediction(prediction)

    def set_reference(self, reference):
        """Set the reference segmentation."""

        if reference is not None:
            self.reference_nifti = sitk.ReadImage(reference)
            super(NiftiEvaluator, self).set_reference(sitk.GetArrayFromImage(self.reference_nifti))
        else:
            self.reference_nifti = None
            super(NiftiEvaluator, self).set_reference(reference)

    def evaluate(self, prediction=None, reference=None, voxel_spacing=None, **metric_kwargs):

        if voxel_spacing is None:
            voxel_spacing = np.array(self.prediction_nifti.GetSpacing())[::-1]
            metric_kwargs["voxel_spacing"] = voxel_spacing

        return super(NiftiEvaluator, self).evaluate(prediction, reference, **metric_kwargs)


def run_evaluation(args):
    prediction, ref, evaluator, metric_kwargs = args
    # evaluate
    evaluator.set_prediction(prediction)
    evaluator.set_reference(ref)
    if evaluator.labels is None:
        evaluator.construct_labels()
    current_scores = evaluator.evaluate(**metric_kwargs)
    if type(prediction) == str:
        current_scores["prediction"] = prediction
    if type(ref) == str:
        current_scores["reference"] = ref
    return current_scores


def aggregate_scores(pred_ref_pairs, evaluator=NiftiEvaluator, labels=None, nanmean=True,
                     json_output_file=None, json_name="", json_description="", json_author="zhx",
                     json_task="Lung Lobe Segmentation", num_threads=2, **metric_kwargs):
    if type(evaluator) == type:
        evaluator = evaluator()

    if labels is not None:
        evaluator.set_labels(labels)

    all_scores = OrderedDict()
    all_scores["all"] = []
    all_scores["mean"] = OrderedDict()

    pred = [i[0] for i in pred_ref_pairs]
    ref = [i[1] for i in pred_ref_pairs]
    p = Pool(num_threads)
    all_res = p.map(run_evaluation, zip(pred, ref, [evaluator]*len(ref), [metric_kwargs]*len(ref)))
    p.close()
    p.join()

    for i in range(len(all_res)):
        all_scores["all"].append(all_res[i])

        # append score list for mean
        for label, score_dict in all_res[i].items():
            if label in ("prediction", "reference"):
                continue
            if label not in all_scores["mean"]:
                all_scores["mean"][label] = OrderedDict()
            for score, value in score_dict.items():
                if score not in all_scores["mean"][label]:
                    all_scores["mean"][label][score] = []
                all_scores["mean"][label][score].append(value)

    for label in all_scores["mean"]:
        for score in all_scores["mean"][label]:
            if nanmean:
                all_scores["mean"][label][score] = float(np.nanmean(all_scores["mean"][label][score]))
            else:
                all_scores["mean"][label][score] = float(np.mean(all_scores["mean"][label][score]))

    # save to file if desired
    # we create a hopefully unique id by hashing the entire output dictionary
    if json_output_file is not None:
        json_dict = OrderedDict()
        json_dict["name"] = json_name
        json_dict["description"] = json_description
        timestamp = datetime.today()
        json_dict["timestamp"] = str(timestamp)
        json_dict["task"] = json_task
        json_dict["author"] = json_author
        json_dict["results"] = all_scores
        json_dict["id"] = hashlib.md5(json.dumps(json_dict).encode("utf-8")).hexdigest()[:12]
        save_json(json_dict, json_output_file)

    return all_scores
