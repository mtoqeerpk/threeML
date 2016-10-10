from __future__ import print_function

import sys, time
import datetime

# This is used for testing purposes

test_ascii_only = False

try:

    from ipywidgets import FloatProgress, HTML, VBox

except ImportError:

    has_widgets = False

else:

    if test_ascii_only:

        has_widgets = False

    else:

        has_widgets = True

from IPython.display import display

from contextlib import contextmanager


class CannotGenerateHTMLBar(RuntimeError):
    pass


@contextmanager
def progress_bar(iterations, width=None):

    # Instance progress bar

    if has_widgets:

        try:

            if width is None:

                width = 50

                # Default is the HTML bar, which only works within a notebook

            this_progress_bar = ProgressBarHTML(iterations, width)

        except:

            if width is None:

                width = 30

            # Running in a terminal. Fall back to the ascii bar

            this_progress_bar = ProgressBarAscii(iterations, width)

    else:

        if width is None:
            
            width = 30

        # No widgets available, fall back to ascii bar

        this_progress_bar = ProgressBarAscii(iterations, width)

    yield this_progress_bar

    this_progress_bar.finish()


@contextmanager
def multiple_progress_bars(iterations, n, width=None, force_html=False):


    # Instance n identical progress bars

    if has_widgets:

        if width is None:

            width = 50

        try:

            # Default is the HTML bar, which only works within a notebook

            this_progress_bars = [ProgressBarHTML(iterations, width) for i in range(n)]

        except:

            if force_html:

                raise CannotGenerateHTMLBar("force_html was set to True, but I couldn't generate an HTML bar")

            # Running in a terminal. Fall back to the ascii bar

            this_progress_bars = [ProgressBarAscii(iterations, width) for i in range(n)]

    else:

        if width is None:

            width = 30

        if force_html:
            raise CannotGenerateHTMLBar("force_html was set to True, but I couldn't generate an HTML bar")

        # No widgets, use Ascii bars

        this_progress_bars = [ProgressBarAscii(iterations, width) for i in range(n)]

    yield this_progress_bars

    for this_progress_bar in this_progress_bars:

        this_progress_bar.finish()



class ProgressBarBase(object):

    def __init__(self, iterations, width):

        # Store the number of iterations

        self._iterations = int(iterations)

        # Store the width (in characters)

        self._width = width

        # Get the start time

        self._start_time = time.time()

        # Current iteration is zero
        self._last_iteration = 0

        # last printed percent
        self._last_printed_percent = 0

        # Setup

        self._setup()

    def _setup(self):

        raise NotImplementedError("Need to override this")

    def animate(self, iteration):

        # We only update the progress bar if the progress has gone backward,
        # or if the progress has increased by at least 1%. This is to avoid
        # updating it too much, which would fill log files in text mode,
        # or slow down the computation in HTML mode

        this_percent = iteration / float(self._iterations) * 100.0

        if this_percent - self._last_printed_percent < 0 or (this_percent - self._last_printed_percent) >= 1:

            self._last_iteration = self._animate(iteration)

            self._last_printed_percent = this_percent

        else:

            self._last_iteration += 1

    def _animate(self, iteration):

        raise NotImplementedError("Need to override this")

    def increase(self):

        self.animate(self._last_iteration + 1)

    def finish(self):

        self._animate(self._iterations)

    def _check_remaining_time(self, current_iteration, delta_t):

        if current_iteration == 0:

            return '--:--'

        # Seconds per iterations
        s_per_iter = delta_t / float(current_iteration)

        # Seconds to go (estimate)
        s_to_go = s_per_iter * (self._iterations - current_iteration)

        # I cast to int so it won't show decimal seconds

        return str(datetime.timedelta(seconds=int(s_to_go)))

    def _get_label(self, current_iteration):

        delta_t = time.time() - self._start_time

        elapsed_iter = min(current_iteration, self._iterations)

        label_text = '%d / %s in %.1f s (%s remaining)' % (elapsed_iter, self._iterations,
                                                           delta_t, self._check_remaining_time(current_iteration,
                                                                                               delta_t))

        return label_text


class ProgressBarHTML(ProgressBarBase):

    def __init__(self, iterations, width):

        super(ProgressBarHTML, self).__init__(iterations, width)

    def _setup(self):

        # Setup the widget, which is a bar between 0 and 100

        self._bar = FloatProgress(min=0, max=100)

        # Set explicitly the bar to 0

        self._bar.value = 0

        # Setup also an HTML label (which will contain the progress, the elapsed time and the foreseen
        # completion time)

        self._label = HTML()
        self._vbox = VBox(children=[self._label, self._bar])

        # Display everything

        display(self._vbox)

        self._animate(0)

    def _animate(self, iteration):

        current_label = self._get_label(iteration)

        self._bar.value = float(iteration) / float(self._iterations) * 100

        self._label.value = current_label

        return iteration


class ProgressBarAscii(ProgressBarBase):

    def __init__(self, iterations, width):

        super(ProgressBarAscii, self).__init__(iterations, width)

    def _setup(self):

        self._fill_char = '*'

        # Display an empty bar
        self._animate(0)

    def _animate(self, current_iteration):

        current_bar = self._generate_bar(current_iteration)
        current_label = self._get_label(current_iteration)

        print('\r%s  %s' % (current_bar, current_label), end='')
        sys.stdout.flush()

        return current_iteration

    def _generate_bar(self, current_iteration):

        # Compute the percentage completed

        elapsed_iter = min(current_iteration, self._iterations)

        new_amount = (elapsed_iter / float(self._iterations)) * 100.0

        percent_done = min(int(round((new_amount / 100.0) * 100.0)), 100)

        # Generate the bar

        all_full = self._width - 2

        num_hashes = int(round((percent_done / 100.0) * all_full))

        bar = '[' + self._fill_char * num_hashes + ' ' * (all_full - num_hashes) + ']'

        # Now place the completed percentage in the middle of the bar

        pct_place = (len(bar) // 2) - len(str(percent_done))
        pct_string = '%d%%' % percent_done

        bar = bar[0:pct_place] + (pct_string + bar[pct_place + len(pct_string):])

        return bar

class ProgressBarOld(object):

    def __init__(self, iterations):

        self.iterations = iterations
        self.prog_bar = '[]'
        self.fill_char = '*'
        self.width = 50
        self.startTime = time.time()
        self.lastIter = 0
        self.__update_amount(0)
        self._last_percent = None

    def animate(self, iter):

        try:

            self.lastIter = iter
            self.update_iteration(iter + 1)

        except:
            # Do not crash in any case. This isn't an important operation
            pass
    
    def increase(self):
        
        self.animate(self.lastIter + 1)

    def _check_remaining_time(self, delta_t):

        # Seconds per iterations
        s_per_iter = delta_t / float(self.lastIter)

        # Seconds to go (estimate)
        s_to_go = s_per_iter * (self.iterations - self.lastIter)

        # I cast to int so it won't show decimal seconds

        return str(datetime.timedelta(seconds=int(s_to_go)))

    def update_iteration(self, elapsed_iter):

        delta_t = time.time() - self.startTime

        elapsed_iter = min(elapsed_iter, self.iterations)

        if elapsed_iter < self.iterations:

            new_amount = (elapsed_iter / float(self.iterations)) * 100.0

            percent_done = min(int(round((new_amount / 100.0) * 100.0)), 100)

            if self._last_percent is None or percent_done > self._last_percent:

                self.__update_amount(percent_done)

                self.prog_bar += '  %d / %s in %.1f s' % (elapsed_iter, self.iterations, delta_t)
                self.prog_bar += ' (%s remaining)' % self._check_remaining_time(delta_t)

                self._last_percent = percent_done

                print('\r', self, end='')
                sys.stdout.flush()

        else:

            self.__update_amount(100)
            self.prog_bar += '  completed in %.1f s' % (time.time() - self.startTime)

    def __update_amount(self, percent_done):

        all_full = self.width - 2
        num_hashes = int(round((percent_done / 100.0) * all_full))
        self.prog_bar = '[' + self.fill_char * num_hashes + ' ' * (all_full - num_hashes) + ']'
        pct_place = (len(self.prog_bar) // 2) - len(str(percent_done))
        pct_string = '%d%%' % percent_done
        self.prog_bar = self.prog_bar[0:pct_place] + \
            (pct_string + self.prog_bar[pct_place + len(pct_string):])

    def __str__(self):
        return str(self.prog_bar)
