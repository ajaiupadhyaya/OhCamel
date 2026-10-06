//! A fixed-capacity ring of samples, oldest first. 720 samples at the
//! default 5-second interval is the hour of history contract II.6 promises.

use std::collections::VecDeque;

pub const CAPACITY: usize = 720;

#[derive(Debug, Clone)]
pub struct Ring<T> {
    buf: VecDeque<T>,
    cap: usize,
}

impl<T: Clone> Ring<T> {
    pub fn new(cap: usize) -> Self {
        Ring {
            buf: VecDeque::with_capacity(cap),
            cap,
        }
    }

    /// Appends `x`, dropping the oldest sample once the ring is full.
    pub fn push(&mut self, x: T) {
        if self.cap == 0 {
            return;
        }
        while self.buf.len() >= self.cap {
            self.buf.pop_front();
        }
        self.buf.push_back(x);
    }

    /// Every sample, oldest first.
    pub fn to_vec(&self) -> Vec<T> {
        self.buf.iter().cloned().collect()
    }

    pub fn latest(&self) -> Option<&T> {
        self.buf.back()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_the_newest_capacity_items_oldest_first() {
        let mut r = Ring::new(CAPACITY);
        for i in 0..=720u32 {
            r.push(i);
        }
        // 721 pushes (0..=720) into 720 slots: 0 is dropped.
        assert_eq!(r.to_vec(), (1..=720).collect::<Vec<u32>>());
        assert_eq!(r.latest(), Some(&720));
    }

    #[test]
    fn empty_and_partial() {
        let mut r: Ring<u8> = Ring::new(3);
        assert_eq!(r.latest(), None);
        assert!(r.to_vec().is_empty());
        r.push(7);
        r.push(8);
        assert_eq!(r.to_vec(), vec![7, 8]);
    }
}
