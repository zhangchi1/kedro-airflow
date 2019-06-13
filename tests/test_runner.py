from unittest.mock import ANY, Mock, call

import pytest
from kedro.io import DataCatalog, LambdaDataSet, MemoryDataSet
from kedro.pipeline import Pipeline
from kedro.pipeline.node import Node

from kedro_airflow.runner import AirflowRunner


def test_create_default_data_set():
    with pytest.raises(ValueError, match="testing"):
        AirflowRunner(None, None, None).create_default_data_set("testing", 0)


def test_create_task():
    def func(a, b):
        return a + b

    orig_catalog = Mock()
    catalog = orig_catalog.shallow_copy()
    catalog.load.side_effect = [1, 2]
    process_context = Mock(return_value=catalog)
    node = Node(func, ["ds_a", "ds_b"], "ds_c")

    task = AirflowRunner(None, process_context, None).create_task(node, orig_catalog)
    task(param=123)
    process_context.assert_called_once_with(catalog, param=123)
    catalog.save.assert_called_once_with("ds_c", 3)


def test_run(mocker):  # pylint: disable=too-many-locals
    # The Nodes
    first_node = Node(lambda: None, [], "a")
    middle_node = Node(lambda a: None, ["a"], "b")
    last_node = Node(lambda b: None, ["b"], [])

    # get turned into tasks by create_task
    first_task = Mock()
    middle_task = Mock()
    last_task = Mock()
    create_task = mocker.patch("kedro_airflow.runner.AirflowRunner.create_task")
    create_task.side_effect = lambda node, catalog: {
        first_node: first_task,
        middle_node: middle_task,
        last_node: last_task,
    }[node]

    # and tasks get turned into operators by the runner
    first_op = Mock()
    middle_op = Mock()
    last_op = Mock()
    operator = mocker.patch("kedro_airflow.runner.PythonOperator")
    operator.side_effect = lambda python_callable, **kwargs: {
        first_task: first_op,
        middle_task: middle_op,
        last_task: last_op,
    }[python_callable]

    def operator_arguments(task_id):
        args = {"lambda-none-a": {"retries": 1}, "lambda-b-none": {"retries": 2}}
        return args.get(task_id, {})

    # actually call the runner to do the conversion
    dag = Mock()
    pipeline = Pipeline([first_node, last_node, middle_node])
    catalog = DataCatalog(
        {
            "a": LambdaDataSet(load=None, save=None),
            "b": LambdaDataSet(load=None, save=None),
        }
    )
    AirflowRunner(dag, None, operator_arguments).run(pipeline, catalog)

    # check the create task calls
    create_task.assert_has_calls(
        [
            call(first_node, catalog),
            call(middle_node, catalog),
            call(last_node, catalog),
        ],
        any_order=True,
    )

    # check the operator constructor calls
    operator.assert_has_calls(
        [
            call(
                dag=dag,
                provide_context=True,
                python_callable=first_task,
                task_id="lambda-none-a",
                retries=1,
            ),
            call(
                dag=dag,
                provide_context=True,
                python_callable=middle_task,
                task_id="lambda-a-b",
            ),
            call(
                dag=dag,
                provide_context=True,
                python_callable=last_task,
                task_id="lambda-b-none",
                retries=2,
            ),
        ],
        any_order=True,
    )

    # check the dependcy hookup
    first_op.set_upstream.assert_not_called()
    middle_op.set_upstream.assert_called_once_with(first_op)
    last_op.set_upstream.assert_called_once_with(middle_op)


def test_operator_arguments(mocker):
    # The Nodes
    first_node = Node(lambda: None, [], "a")
    last_node = Node(lambda: None, [], "b")

    # get turned into tasks and then into operators by the runner
    operator = mocker.patch("kedro_airflow.runner.PythonOperator")

    def operator_arguments(task_id):
        args = {"lambda-none-a": {"retries": 1}}
        return args.get(task_id, {})

    # actually call the runner to do the conversion
    dag = Mock()
    pipeline = Pipeline([first_node, last_node])
    catalog = DataCatalog({"a": None, "b": None})
    AirflowRunner(dag, None, operator_arguments).run(pipeline, catalog)

    # check the operator constructor calls
    operator.assert_has_calls(
        [
            call(
                dag=dag,
                provide_context=True,
                python_callable=ANY,
                task_id="lambda-none-a",
                retries=1,
            ),
            call(
                dag=dag,
                provide_context=True,
                python_callable=ANY,
                task_id="lambda-none-b",
            ),
        ],
        any_order=True,
    )


def test_no_default_datasets():
    pipeline = Pipeline([Node(lambda: None, [], "fred")])
    catalog = DataCatalog()
    with pytest.raises(ValueError, match="'fred' is not registered"):
        AirflowRunner(None, None, {}).run(pipeline, catalog)


def test_no_memory_datasets():
    pipeline = Pipeline([Node(lambda: None, [], "fred")])
    catalog = DataCatalog({"fred": MemoryDataSet()})
    with pytest.raises(ValueError, match="memory data sets: 'fred'"):
        AirflowRunner(None, None, {}).run(pipeline, catalog)
