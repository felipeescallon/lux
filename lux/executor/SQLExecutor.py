import pandas
from lux.vis.VisList import VisList
from lux.vis.Vis import Vis
from lux.core.frame import LuxDataFrame
from lux.executor.Executor import Executor
from lux.utils import utils
from lux.utils.utils import check_import_lux_widget, check_if_id_like
import lux

import math


class SQLExecutor(Executor):
    """
    Given a Vis objects with complete specifications, fetch and process data using SQL operations.
    """

    def __init__(self):
        self.name = "SQLExecutor"
        self.selection = []
        self.tables = []
        self.filters = ""

    def __repr__(self):
        return f"<SQLExecutor>"

    @staticmethod
    def execute_sampling(ldf: LuxDataFrame):
        SAMPLE_FLAG = lux.config.sampling
        SAMPLE_START = lux.config.sampling_start
        SAMPLE_CAP = lux.config.sampling_cap
        SAMPLE_FRAC = 0.2

        length_query = pandas.read_sql(
            "SELECT COUNT(*) as length FROM {}".format(ldf.table_name),
            lux.config.SQLconnection,
        )
        limit = int(list(length_query["length"])[0]) * SAMPLE_FRAC
        ldf._sampled = pandas.read_sql(
            "SELECT * from {} LIMIT {}".format(ldf.table_name, str(limit)), lux.config.SQLconnection
        )

    @staticmethod
    def execute(view_collection: VisList, ldf: LuxDataFrame):
        """
        Given a VisList, fetch the data required to render the view
        1) Generate Necessary WHERE clauses
        2) Query necessary data, applying appropriate aggregation for the chart type
        3) populates vis' data with a DataFrame with relevant results
        """

        for view in view_collection:
            # choose execution method depending on vis mark type

            # when mark is empty, deal with lazy execution by filling the data with a small sample of the dataframe
            if view.mark == "":
                SQLExecutor.execute_sampling(ldf)
                view._vis_data = ldf._sampled
            if view.mark == "scatter":
                where_clause, filterVars = SQLExecutor.execute_filter(view)
                length_query = pandas.read_sql(
                    "SELECT COUNT(*) as length FROM {} {}".format(ldf.table_name, where_clause),
                    lux.config.SQLconnection,
                )
                view_data_length = list(length_query["length"])[0]
                if len(view.get_attr_by_channel("color")) == 1 or view_data_length < 5000:
                    # NOTE: might want to have a check somewhere to not use categorical variables with greater than some number of categories as a Color variable----------------
                    has_color = True
                    SQLExecutor.execute_scatter(view, ldf)
                else:
                    view._mark = "heatmap"
                    SQLExecutor.execute_2D_binning(view, ldf)
            elif view.mark == "bar" or view.mark == "line":
                SQLExecutor.execute_aggregate(view, ldf)
            elif view.mark == "histogram":
                SQLExecutor.execute_binning(view, ldf)

    @staticmethod
    def execute_scatter(view: Vis, ldf: LuxDataFrame):
        """
        Given a scatterplot vis and a Lux Dataframe, fetch the data required to render the vis.
        1) Generate WHERE clause for the SQL query
        2) Check number of datapoints to be included in the query
        3) If the number of datapoints exceeds 10000, perform a random sample from the original data
        4) Query datapoints needed for the scatterplot visualization
        5) return a DataFrame with relevant results

        Parameters
        ----------
        vislist: list[lux.Vis]
            vis list that contains lux.Vis objects for visualization.
        ldf : lux.core.frame
            LuxDataFrame with specified intent.

        Returns
        -------
        None
        """

        attributes = set([])
        for clause in view._inferred_intent:
            if clause.attribute:
                if clause.attribute != "Record":
                    attributes.add(clause.attribute)
        where_clause, filterVars = SQLExecutor.execute_filter(view)

        length_query = pandas.read_sql(
            "SELECT COUNT(*) as length FROM {} {}".format(ldf.table_name, where_clause),
            lux.config.SQLconnection,
        )

        def add_quotes(var_name):
            return '"' + var_name + '"'

        required_variables = attributes | set(filterVars)
        required_variables = map(add_quotes, required_variables)
        required_variables = ",".join(required_variables)
        row_count = list(
            pandas.read_sql(
                f"SELECT COUNT(*) FROM {ldf.table_name} {where_clause}",
                lux.config.SQLconnection,
            )["count"]
        )[0]
        if row_count > lux.config.sampling_cap:
            query = f"SELECT {required_variables} FROM {ldf.table_name} {where_clause} ORDER BY random() LIMIT 10000"
        else:
            query = "SELECT {} FROM {} {}".format(required_variables, ldf.table_name, where_clause)
        data = pandas.read_sql(query, lux.config.SQLconnection)
        view._vis_data = utils.pandas_to_lux(data)
        view._vis_data.length = list(length_query["length"])[0]

        ldf._message.add_unique(
            f"Large scatterplots detected: Lux is automatically binning scatterplots to heatmaps.",
            priority=98,
        )

    @staticmethod
    def execute_aggregate(view: Vis, ldf: LuxDataFrame, isFiltered=True):
        """
        Aggregate data points on an axis for bar or line charts
        Parameters
        ----------
        vis: lux.Vis
            lux.Vis object that represents a visualization
        ldf : lux.core.frame
            LuxDataFrame with specified intent.
        isFiltered: boolean
            boolean that represents whether a vis has had a filter applied to its data
        Returns
        -------
        None
        """
        x_attr = view.get_attr_by_channel("x")[0]
        y_attr = view.get_attr_by_channel("y")[0]
        has_color = False
        groupby_attr = ""
        measure_attr = ""
        if x_attr.aggregation is None or y_attr.aggregation is None:
            return
        if y_attr.aggregation != "":
            groupby_attr = x_attr
            measure_attr = y_attr
            agg_func = y_attr.aggregation
        if x_attr.aggregation != "":
            groupby_attr = y_attr
            measure_attr = x_attr
            agg_func = x_attr.aggregation
        if groupby_attr.attribute in ldf.unique_values.keys():
            attr_unique_vals = ldf.unique_values[groupby_attr.attribute]
        # checks if color is specified in the Vis
        if len(view.get_attr_by_channel("color")) == 1:
            color_attr = view.get_attr_by_channel("color")[0]
            color_attr_vals = ldf.unique_values[color_attr.attribute]
            color_cardinality = len(color_attr_vals)
            # NOTE: might want to have a check somewhere to not use categorical variables with greater than some number of categories as a Color variable----------------
            has_color = True
        else:
            color_cardinality = 1
        if measure_attr != "":
            # barchart case, need count data for each group
            if measure_attr.attribute == "Record":
                where_clause, filterVars = SQLExecutor.execute_filter(view)

                length_query = pandas.read_sql(
                    "SELECT COUNT(*) as length FROM {} {}".format(ldf.table_name, where_clause),
                    lux.config.SQLconnection,
                )
                # generates query for colored barchart case
                if has_color:
                    count_query = 'SELECT "{}", "{}", COUNT("{}") FROM {} {} GROUP BY "{}", "{}"'.format(
                        groupby_attr.attribute,
                        color_attr.attribute,
                        groupby_attr.attribute,
                        ldf.table_name,
                        where_clause,
                        groupby_attr.attribute,
                        color_attr.attribute,
                    )
                    view._vis_data = pandas.read_sql(count_query, lux.config.SQLconnection)
                    view._vis_data = view._vis_data.rename(columns={"count": "Record"})
                    view._vis_data = utils.pandas_to_lux(view._vis_data)
                # generates query for normal barchart case
                else:
                    count_query = 'SELECT "{}", COUNT("{}") FROM {} {} GROUP BY "{}"'.format(
                        groupby_attr.attribute,
                        groupby_attr.attribute,
                        ldf.table_name,
                        where_clause,
                        groupby_attr.attribute,
                    )
                    view._vis_data = pandas.read_sql(count_query, lux.config.SQLconnection)
                    view._vis_data = view._vis_data.rename(columns={"count": "Record"})
                    view._vis_data = utils.pandas_to_lux(view._vis_data)
                view._vis_data.length = list(length_query["length"])[0]
            # aggregate barchart case, need aggregate data (mean, sum, max) for each group
            else:
                where_clause, filterVars = SQLExecutor.execute_filter(view)

                length_query = pandas.read_sql(
                    "SELECT COUNT(*) as length FROM {} {}".format(ldf.table_name, where_clause),
                    lux.config.SQLconnection,
                )
                # generates query for colored barchart case
                if has_color:
                    if agg_func == "mean":
                        agg_query = (
                            'SELECT "{}", "{}", AVG("{}") as "{}" FROM {} {} GROUP BY "{}", "{}"'.format(
                                groupby_attr.attribute,
                                color_attr.attribute,
                                measure_attr.attribute,
                                measure_attr.attribute,
                                ldf.table_name,
                                where_clause,
                                groupby_attr.attribute,
                                color_attr.attribute,
                            )
                        )
                        view._vis_data = pandas.read_sql(agg_query, lux.config.SQLconnection)

                        view._vis_data = utils.pandas_to_lux(view._vis_data)
                    if agg_func == "sum":
                        agg_query = (
                            'SELECT "{}", "{}", SUM("{}") as "{}" FROM {} {} GROUP BY "{}", "{}"'.format(
                                groupby_attr.attribute,
                                color_attr.attribute,
                                measure_attr.attribute,
                                measure_attr.attribute,
                                ldf.table_name,
                                where_clause,
                                groupby_attr.attribute,
                                color_attr.attribute,
                            )
                        )
                        view._vis_data = pandas.read_sql(agg_query, lux.config.SQLconnection)
                        view._vis_data = utils.pandas_to_lux(view._vis_data)
                    if agg_func == "max":
                        agg_query = (
                            'SELECT "{}", "{}", MAX("{}") as "{}" FROM {} {} GROUP BY "{}", "{}"'.format(
                                groupby_attr.attribute,
                                color_attr.attribute,
                                measure_attr.attribute,
                                measure_attr.attribute,
                                ldf.table_name,
                                where_clause,
                                groupby_attr.attribute,
                                color_attr.attribute,
                            )
                        )
                        view._vis_data = pandas.read_sql(agg_query, lux.config.SQLconnection)
                        view._vis_data = utils.pandas_to_lux(view._vis_data)
                # generates query for normal barchart case
                else:
                    if agg_func == "mean":
                        agg_query = 'SELECT "{}", AVG("{}") as "{}" FROM {} {} GROUP BY "{}"'.format(
                            groupby_attr.attribute,
                            measure_attr.attribute,
                            measure_attr.attribute,
                            ldf.table_name,
                            where_clause,
                            groupby_attr.attribute,
                        )
                        view._vis_data = pandas.read_sql(agg_query, lux.config.SQLconnection)
                        view._vis_data = utils.pandas_to_lux(view._vis_data)
                    if agg_func == "sum":
                        agg_query = 'SELECT "{}", SUM("{}") as "{}" FROM {} {} GROUP BY "{}"'.format(
                            groupby_attr.attribute,
                            measure_attr.attribute,
                            measure_attr.attribute,
                            ldf.table_name,
                            where_clause,
                            groupby_attr.attribute,
                        )
                        view._vis_data = pandas.read_sql(agg_query, lux.config.SQLconnection)
                        view._vis_data = utils.pandas_to_lux(view._vis_data)
                    if agg_func == "max":
                        agg_query = 'SELECT "{}", MAX("{}") as "{}" FROM {} {} GROUP BY "{}"'.format(
                            groupby_attr.attribute,
                            measure_attr.attribute,
                            measure_attr.attribute,
                            ldf.table_name,
                            where_clause,
                            groupby_attr.attribute,
                        )
                        view._vis_data = pandas.read_sql(agg_query, lux.config.SQLconnection)
                        view._vis_data = utils.pandas_to_lux(view._vis_data)
            result_vals = list(view._vis_data[groupby_attr.attribute])
            # create existing group by attribute combinations if color is specified
            # this is needed to check what combinations of group_by_attr and color_attr values have a non-zero number of elements in them
            if has_color:
                res_color_combi_vals = []
                result_color_vals = list(view._vis_data[color_attr.attribute])
                for i in range(0, len(result_vals)):
                    res_color_combi_vals.append([result_vals[i], result_color_vals[i]])
            # For filtered aggregation that have missing groupby-attribute values, set these aggregated value as 0, since no datapoints
            if isFiltered or has_color and attr_unique_vals:
                N_unique_vals = len(attr_unique_vals)
                if len(result_vals) != N_unique_vals * color_cardinality:
                    columns = view._vis_data.columns
                    if has_color:
                        df = pandas.DataFrame(
                            {
                                columns[0]: attr_unique_vals * color_cardinality,
                                columns[1]: pandas.Series(color_attr_vals).repeat(N_unique_vals),
                            }
                        )
                        view._vis_data = view._vis_data.merge(
                            df,
                            on=[columns[0], columns[1]],
                            how="right",
                            suffixes=["", "_right"],
                        )
                        for col in columns[2:]:
                            view._vis_data[col] = view._vis_data[col].fillna(0)  # Triggers __setitem__
                        assert len(list(view._vis_data[groupby_attr.attribute])) == N_unique_vals * len(
                            color_attr_vals
                        ), f"Aggregated data missing values compared to original range of values of `{groupby_attr.attribute, color_attr.attribute}`."
                        view._vis_data = view._vis_data.iloc[
                            :, :3
                        ]  # Keep only the three relevant columns not the *_right columns resulting from merge
                    else:
                        df = pandas.DataFrame({columns[0]: attr_unique_vals})

                        view._vis_data = view._vis_data.merge(
                            df, on=columns[0], how="right", suffixes=["", "_right"]
                        )

                        for col in columns[1:]:
                            view._vis_data[col] = view._vis_data[col].fillna(0)
                        assert (
                            len(list(view._vis_data[groupby_attr.attribute])) == N_unique_vals
                        ), f"Aggregated data missing values compared to original range of values of `{groupby_attr.attribute}`."
            view._vis_data = view._vis_data.sort_values(by=groupby_attr.attribute, ascending=True)
            view._vis_data = view._vis_data.reset_index()
            view._vis_data = view._vis_data.drop(columns="index")
            view._vis_data.length = list(length_query["length"])[0]

    @staticmethod
    def execute_binning(view: Vis, ldf: LuxDataFrame):
        """
        Binning of data points for generating histograms
        Parameters
        ----------
        vis: lux.Vis
            lux.Vis object that represents a visualization
        ldf : lux.core.frame
            LuxDataFrame with specified intent.
        Returns
        -------
        None
        """
        import numpy as np

        bin_attribute = list(filter(lambda x: x.bin_size != 0, view._inferred_intent))[0]

        num_bins = bin_attribute.bin_size
        attr_min = ldf._min_max[bin_attribute.attribute][0]
        attr_max = ldf._min_max[bin_attribute.attribute][1]
        attr_type = type(ldf.unique_values[bin_attribute.attribute][0])

        # get filters if available
        where_clause, filterVars = SQLExecutor.execute_filter(view)

        length_query = pandas.read_sql(
            "SELECT COUNT(*) as length FROM {} {}".format(ldf.table_name, where_clause),
            lux.config.SQLconnection,
        )
        # need to calculate the bin edges before querying for the relevant data
        bin_width = (attr_max - attr_min) / num_bins
        upper_edges = []
        for e in range(1, num_bins):
            curr_edge = attr_min + e * bin_width
            if attr_type == int:
                upper_edges.append(str(math.ceil(curr_edge)))
            else:
                upper_edges.append(str(curr_edge))
        upper_edges = ",".join(upper_edges)
        view_filter, filter_vars = SQLExecutor.execute_filter(view)
        bin_count_query = "SELECT width_bucket, COUNT(width_bucket) FROM (SELECT width_bucket(CAST (\"{}\" AS FLOAT), '{}') FROM {} {}) as Buckets GROUP BY width_bucket ORDER BY width_bucket".format(
            bin_attribute.attribute,
            "{" + upper_edges + "}",
            ldf.table_name,
            where_clause,
        )

        bin_count_data = pandas.read_sql(bin_count_query, lux.config.SQLconnection)
        if not bin_count_data["width_bucket"].isnull().values.any():
            # np.histogram breaks if data contain NaN

            # counts,binEdges = np.histogram(ldf[bin_attribute.attribute],bins=bin_attribute.bin_size)
            # binEdges of size N+1, so need to compute binCenter as the bin location
            upper_edges = [float(i) for i in upper_edges.split(",")]
            if attr_type == int:
                bin_centers = np.array([math.ceil((attr_min + attr_min + bin_width) / 2)])
            else:
                bin_centers = np.array([(attr_min + attr_min + bin_width) / 2])
            bin_centers = np.append(
                bin_centers,
                np.mean(np.vstack([upper_edges[0:-1], upper_edges[1:]]), axis=0),
            )
            if attr_type == int:
                bin_centers = np.append(
                    bin_centers,
                    math.ceil((upper_edges[len(upper_edges) - 1] + attr_max) / 2),
                )
            else:
                bin_centers = np.append(bin_centers, (upper_edges[len(upper_edges) - 1] + attr_max) / 2)

            if len(bin_centers) > len(bin_count_data):
                bucket_lables = bin_count_data["width_bucket"].unique()
                for i in range(0, len(bin_centers)):
                    if i not in bucket_lables:
                        bin_count_data = bin_count_data.append(
                            pandas.DataFrame([[i, 0]], columns=bin_count_data.columns)
                        )
            view._vis_data = pandas.DataFrame(
                np.array([bin_centers, list(bin_count_data["count"])]).T,
                columns=[bin_attribute.attribute, "Number of Records"],
            )
            view._vis_data = utils.pandas_to_lux(view.data)
            view._vis_data.length = list(length_query["length"])[0]

    @staticmethod
    def execute_2D_binning(view: Vis, ldf: LuxDataFrame):
        import numpy as np

        x_attribute = list(filter(lambda x: x.channel == "x", view._inferred_intent))[0]

        y_attribute = list(filter(lambda x: x.channel == "y", view._inferred_intent))[0]

        num_bins = lux.config.heatmap_bin_size
        x_attr_min = ldf._min_max[x_attribute.attribute][0]
        x_attr_max = ldf._min_max[x_attribute.attribute][1]
        x_attr_type = type(ldf.unique_values[x_attribute.attribute][0])

        y_attr_min = ldf._min_max[y_attribute.attribute][0]
        y_attr_max = ldf._min_max[y_attribute.attribute][1]
        y_attr_type = type(ldf.unique_values[y_attribute.attribute][0])

        # get filters if available
        where_clause, filterVars = SQLExecutor.execute_filter(view)

        # need to calculate the bin edges before querying for the relevant data
        x_bin_width = (x_attr_max - x_attr_min) / num_bins
        y_bin_width = (y_attr_max - y_attr_min) / num_bins

        x_upper_edges = []
        y_upper_edges = []
        for e in range(0, num_bins):
            x_curr_edge = x_attr_min + e * x_bin_width
            y_curr_edge = y_attr_min + e * y_bin_width
            # get upper edges for x attribute bins
            if x_attr_type == int:
                x_upper_edges.append(math.ceil(x_curr_edge))
            else:
                x_upper_edges.append(x_curr_edge)
            # get upper edges for y attribute bins
            if y_attr_type == int:
                y_upper_edges.append(str(math.ceil(y_curr_edge)))
            else:
                y_upper_edges.append(str(y_curr_edge))
        x_upper_edges_string = [str(int) for int in x_upper_edges]
        x_upper_edges_string = ",".join(x_upper_edges_string)
        y_upper_edges_string = ",".join(y_upper_edges)

        bin_count_query = "SELECT width_bucket1, width_bucket2, count(*) FROM (SELECT width_bucket(CAST (\"{}\" AS FLOAT), '{}') as width_bucket1, width_bucket(CAST (\"{}\" AS FLOAT), '{}') as width_bucket2 FROM {} {}) as foo GROUP BY width_bucket1, width_bucket2".format(
            x_attribute.attribute,
            "{" + x_upper_edges_string + "}",
            y_attribute.attribute,
            "{" + y_upper_edges_string + "}",
            ldf.table_name,
            where_clause,
        )

        # data = pandas.read_sql(bin_count_query, lux.config.SQLconnection)

        data = pandas.read_sql(bin_count_query, lux.config.SQLconnection)
        # data = data[data["width_bucket1"] != num_bins - 1]
        # data = data[data["width_bucket2"] != num_bins - 1]
        if len(data) > 0:
            data["xBinStart"] = data.apply(
                lambda row: float(x_upper_edges[int(row["width_bucket1"]) - 1]) - x_bin_width, axis=1
            )
            data["xBinEnd"] = data.apply(
                lambda row: float(x_upper_edges[int(row["width_bucket1"]) - 1]), axis=1
            )
            data["yBinStart"] = data.apply(
                lambda row: float(y_upper_edges[int(row["width_bucket2"]) - 1]) - y_bin_width, axis=1
            )
            data["yBinEnd"] = data.apply(
                lambda row: float(y_upper_edges[int(row["width_bucket2"]) - 1]), axis=1
            )
        view._vis_data = utils.pandas_to_lux(data)

    @staticmethod
    def execute_filter(view: Vis):
        """
        Helper function to convert a Vis' filter specification to a SQL where clause.
        Takes in a Vis object and returns an appropriate SQL WHERE clause based on the filters specified in the vis' _inferred_intent.

        Parameters
        ----------
        vis: lux.Vis
            lux.Vis object that represents a visualization

        Returns
        -------
        where_clause: string
            String representation of a SQL WHERE clause
        filter_vars: list of strings
            list of variables that have been used as filters
        """
        where_clause = []
        filters = utils.get_filter_specs(view._inferred_intent)
        filter_vars = []
        if filters:
            for f in range(0, len(filters)):
                if f == 0:
                    where_clause.append("WHERE")
                else:
                    where_clause.append("AND")
                curr_value = str(filters[f].value)
                curr_value = curr_value.replace("'", "''")
                where_clause.extend(
                    [
                        '"' + str(filters[f].attribute) + '"',
                        str(filters[f].filter_op),
                        "'" + curr_value + "'",
                    ]
                )
                if filters[f].attribute not in filter_vars:
                    filter_vars.append(filters[f].attribute)

        attributes = utils.get_attrs_specs(view._inferred_intent)

        # need to ensure that no null values are included in the data
        # null values breaks binning queries
        for a in attributes:
            if a.attribute != "Record":
                if where_clause == []:
                    where_clause.append("WHERE")
                else:
                    where_clause.append("AND")
                where_clause.extend(
                    [
                        '"' + str(a.attribute) + '"',
                        "IS NOT NULL",
                    ]
                )

        if where_clause == []:
            return ("", [])
        else:
            where_clause = " ".join(where_clause)
        return (where_clause, filter_vars)

    #######################################################
    ########## Metadata, type, model schema ###############
    #######################################################

    def compute_dataset_metadata(self, ldf: LuxDataFrame):
        """
        Function which computes the metadata required for the Lux recommendation system.
        Populates the metadata parameters of the specified Lux DataFrame.

        Parameters
        ----------
        ldf: lux.LuxDataFrame
            lux.LuxDataFrame object whose metadata will be calculated

        Returns
        -------
        None
        """
        self.get_SQL_attributes(ldf)
        for attr in list(ldf.columns):
            ldf[attr] = None
        ldf._data_type = {}
        #####NOTE: since we aren't expecting users to do much data processing with the SQL database, should we just keep this
        #####      in the initialization and do it just once
        self.compute_data_type(ldf)
        self.compute_stats(ldf)

    def get_SQL_attributes(self, ldf: LuxDataFrame):
        """
        Retrieves the names of variables within a specified Lux DataFrame's Postgres SQL table.
        Uses these variables to populate the Lux DataFrame's columns list.

        Parameters
        ----------
        ldf: lux.LuxDataFrame
            lux.LuxDataFrame object whose columns will be populated

        Returns
        -------
        None
        """
        if "." in ldf.table_name:
            table_name = ldf.table_name[self.table_name.index(".") + 1 :]
        else:
            table_name = ldf.table_name
        attr_query = "SELECT column_name FROM INFORMATION_SCHEMA.COLUMNS where TABLE_NAME = '{}'".format(
            table_name
        )
        attributes = list(pandas.read_sql(attr_query, lux.config.SQLconnection)["column_name"])
        for attr in attributes:
            ldf[attr] = None

    def compute_stats(self, ldf: LuxDataFrame):
        """
        Function which computes the min and max values for each variable within the specified Lux DataFrame's SQL table.
        Populates the metadata parameters of the specified Lux DataFrame.

        Parameters
        ----------
        ldf: lux.LuxDataFrame
            lux.LuxDataFrame object whose metadata will be calculated

        Returns
        -------
        None
        """
        # precompute statistics
        ldf.unique_values = {}
        ldf._min_max = {}
        length_query = pandas.read_sql(
            "SELECT COUNT(*) as length FROM {}".format(ldf.table_name),
            lux.config.SQLconnection,
        )
        ldf.length = list(length_query["length"])[0]

        self.get_unique_values(ldf)
        # ldf.get_cardinality()
        for attribute in ldf.columns:
            if ldf._data_type[attribute] == "quantitative":
                min_max_query = pandas.read_sql(
                    'SELECT MIN("{}") as min, MAX("{}") as max FROM {}'.format(
                        attribute, attribute, ldf.table_name
                    ),
                    lux.config.SQLconnection,
                )
                ldf._min_max[attribute] = (
                    list(min_max_query["min"])[0],
                    list(min_max_query["max"])[0],
                )

    def get_cardinality(self, ldf: LuxDataFrame):
        """
        Function which computes the cardinality for each variable within the specified Lux DataFrame's SQL table.
        Populates the metadata parameters of the specified Lux DataFrame.

        Parameters
        ----------
        ldf: lux.LuxDataFrame
            lux.LuxDataFrame object whose metadata will be calculated

        Returns
        -------
        None
        """
        cardinality = {}
        for attr in list(ldf.columns):
            card_query = 'SELECT Count(Distinct("{}")) FROM {} WHERE "{}" IS NOT NULL'.format(
                attr, ldf.table_name, attr
            )
            card_data = pandas.read_sql(
                card_query,
                lux.config.SQLconnection,
            )
            cardinality[attr] = list(card_data["count"])[0]
        ldf.cardinality = cardinality

    def get_unique_values(self, ldf: LuxDataFrame):
        """
        Function which collects the unique values for each variable within the specified Lux DataFrame's SQL table.
        Populates the metadata parameters of the specified Lux DataFrame.

        Parameters
        ----------
        ldf: lux.LuxDataFrame
            lux.LuxDataFrame object whose metadata will be calculated

        Returns
        -------
        None
        """
        unique_vals = {}
        for attr in list(ldf.columns):
            unique_query = 'SELECT Distinct("{}") FROM {} WHERE "{}" IS NOT NULL'.format(
                attr, ldf.table_name, attr
            )
            unique_data = pandas.read_sql(
                unique_query,
                lux.config.SQLconnection,
            )
            unique_vals[attr] = list(unique_data[attr])
        ldf.unique_values = unique_vals

    def compute_data_type(self, ldf: LuxDataFrame):
        """
        Function which the equivalent Pandas data type of each variable within the specified Lux DataFrame's SQL table.
        Populates the metadata parameters of the specified Lux DataFrame.

        Parameters
        ----------
        ldf: lux.LuxDataFrame
            lux.LuxDataFrame object whose metadata will be calculated

        Returns
        -------
        None
        """
        data_type = {}
        sql_dtypes = {}
        self.get_cardinality(ldf)
        if "." in ldf.table_name:
            table_name = ldf.table_name[ldf.table_name.index(".") + 1 :]
        else:
            table_name = ldf.table_name
        # get the data types of the attributes in the SQL table
        for attr in list(ldf.columns):
            datatype_query = "SELECT DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{}' AND COLUMN_NAME = '{}'".format(
                table_name, attr
            )
            datatype = list(pandas.read_sql(datatype_query, lux.config.SQLconnection)["data_type"])[0]

            sql_dtypes[attr] = datatype
        for attr in list(ldf.columns):
            if str(attr).lower() in ["month", "year"]:
                data_type[attr] = "temporal"
            elif sql_dtypes[attr] in [
                "character",
                "character varying",
                "boolean",
                "uuid",
                "text",
            ]:
                data_type[attr] = "nominal"
            elif sql_dtypes[attr] in [
                "integer",
                "numeric",
                "decimal",
                "bigint",
                "real",
                "smallint",
                "smallserial",
                "serial",
                "double precision",
            ]:
                if ldf.cardinality[attr] < 13:
                    data_type[attr] = "nominal"
                elif check_if_id_like(ldf, attr):
                    ldf._data_type[attr] = "id"
                else:
                    data_type[attr] = "quantitative"
            elif "time" in sql_dtypes[attr] or "date" in sql_dtypes[attr]:
                data_type[attr] = "temporal"
        ldf._data_type = data_type
